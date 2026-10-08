"""Sprint 2 - limpeza e exploração do 156.

Entrada:  dados/brutos/156_2023.csv, 156_2024.csv, 156_2025.csv
Saídas:   dados/processados/156_limpo.parquet
          saidas/tabela_perdas.csv, situacoes_unicas.csv, grupos_servico.csv,
          saidas/estat_*.csv, volume_mensal.png, mapa_densidade.png
"""
import pandas as pd
import numpy as np
from config import (ANOS, BRUTO, PROCESSADO, SAIDAS, GRUPOS_RECORTE, RPA_PILOTO,
                    LAT_MIN, LAT_MAX, LON_MIN, LON_MAX, BAIRROS_GEOJSON)
from utils import TabelaPerdas, norm_texto, norm_coluna, expandir_abrev

perdas = TabelaPerdas()


# ---------- leitura robusta ----------
def ler_csv(caminho):
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        for sep in (";", ","):
            try:
                d = pd.read_csv(caminho, sep=sep, encoding=enc, low_memory=False, dtype=str)
                if d.shape[1] > 3:
                    return d
            except Exception:
                continue
    raise ValueError(f"Não consegui ler {caminho}")


def carregar():
    partes = []
    for ano in ANOS:
        d = ler_csv(BRUTO / f"156_{ano}.csv")
        d.columns = [norm_coluna(c) for c in d.columns]
        d["ANO_ARQUIVO"] = ano
        print(f"{ano}: {d.shape[0]:,} linhas, {d.shape[1]} colunas")
        partes.append(d)
    comuns = set.intersection(*[set(p.columns) for p in partes])
    todas = set.union(*[set(p.columns) for p in partes])
    if todas - comuns:
        print("ATENÇÃO: colunas que não existem em todos os anos:", sorted(todas - comuns))
    return pd.concat(partes, ignore_index=True)


# ---------- datas ----------
def parse_data(s):
    s = s.astype("string").str.strip()
    iso = s.str.match(r"^\d{4}-\d{2}-\d{2}", na=False)
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    out[iso] = pd.to_datetime(s[iso].str[:10], format="%Y-%m-%d", errors="coerce")
    out[~iso] = pd.to_datetime(s[~iso].str[:10], format="%d/%m/%Y", errors="coerce")
    return out


def achar_coluna(df, *candidatas):
    for c in candidatas:
        if c in df.columns:
            return c
    raise KeyError(f"Nenhuma destas colunas existe: {candidatas}. Colunas: {list(df.columns)}")


def main():
    SAIDAS.mkdir(exist_ok=True)
    PROCESSADO.mkdir(parents=True, exist_ok=True)
    df = carregar()
    perdas.log("0 empilhar 2023-2025", len(df), len(df))

    # Etapa 1: filtrar recorte
    col_grupo = achar_coluna(df, "GRUPOSERVICO_DESCRICAO")
    df["_GRUPO"] = df[col_grupo].map(norm_texto)
    df["_GRUPO"].value_counts().rename_axis("grupo").reset_index(name="n") \
        .to_csv(SAIDAS / "grupos_servico.csv", index=False)
    print("\nTop grupos de serviço (confira se os termos de GRUPOS_RECORTE batem):")
    print(df["_GRUPO"].value_counts().head(15))
    n = len(df)
    mask = df["_GRUPO"].fillna("").apply(lambda g: any(t in g for t in GRUPOS_RECORTE))
    df = df[mask].copy()
    perdas.log("1 filtrar recorte", n, len(df), f"termos={GRUPOS_RECORTE}")
    if df.empty:
        raise SystemExit("Filtro zerou a base: ajuste GRUPOS_RECORTE com base em saidas/grupos_servico.csv")

    # Etapa 2: duplicatas exatas
    n = len(df)
    df = df.drop_duplicates()
    perdas.log("2 duplicatas exatas", n, len(df))

    # Etapa 3: datas
    c_dem = achar_coluna(df, "DATA_DEMANDA")
    c_ult = achar_coluna(df, "DATA_ULT_SITUACAO", "DATA_ULTIMA_SITUACAO")
    df["DATA_DEMANDA"] = parse_data(df[c_dem])
    df["DATA_ULT_SITUACAO"] = parse_data(df[c_ult])
    n = len(df)
    df = df[df["DATA_DEMANDA"].dt.year == df["ANO_ARQUIVO"]].copy()
    perdas.log("3 datas inválidas/fora do ano", n, len(df))

    # Etapa 4: situações (para classificar em FINAL/ABERTA)
    c_sit = achar_coluna(df, "SITUACAO")
    df["SITUACAO"] = df[c_sit].map(norm_texto)
    sit = df["SITUACAO"].value_counts().rename_axis("situacao").reset_index(name="n")
    sit["tipo"] = ""   # preencher manualmente: FINAL ou ABERTA
    sit.to_csv(SAIDAS / "situacoes_unicas.csv", index=False)
    print("\nSituações encontradas (classifique em saidas/situacoes_unicas.csv):")
    print(sit.to_string(index=False))

    # Etapas 5/6: coordenadas (não descarta; marca com flag)
    c_lat = achar_coluna(df, "LATITUDE", "LAT")
    c_lon = achar_coluna(df, "LONGITUDE", "LON", "LONG")
    for novo, antigo in (("LAT", c_lat), ("LON", c_lon)):
        df[novo] = pd.to_numeric(df[antigo].astype("string").str.replace(",", ".", regex=False),
                                 errors="coerce")
    n_sem = int(df[["LAT", "LON"]].isna().any(axis=1).sum())
    dentro = df["LAT"].between(LAT_MIN, LAT_MAX) & df["LON"].between(LON_MIN, LON_MAX)
    df["COORD_VALIDA"] = dentro
    perdas.log("5-6 coordenadas", len(df), len(df),
               f"{n_sem:,} sem coordenada; {int((~dentro).sum()) - n_sem:,} fora do Recife; "
               f"mantidas com COORD_VALIDA=False (saem só na base de pontos)")

    # Etapa 7: padronizar texto
    for c in ("BAIRRO", "LOGRADOURO"):
        if c in df.columns:
            df[c] = df[c].map(norm_texto).map(expandir_abrev)
    if "NUMERO" in df.columns:
        df["NUMERO"] = df["NUMERO"].map(norm_texto).replace({"SN": pd.NA, "S/N": pd.NA})
    if "RPA" in df.columns:
        df["RPA_NUM"] = pd.to_numeric(df["RPA"].astype("string").str.extract(r"(\d+)")[0], errors="coerce")
        if RPA_PILOTO is not None:
            n = len(df)
            df = df[df["RPA_NUM"] == RPA_PILOTO].copy()
            perdas.log(f"7b recorte RPA {RPA_PILOTO}", n, len(df))

    df = df.drop(columns=["_GRUPO"]).reset_index(drop=True)
    df.insert(0, "ID_LINHA", np.arange(len(df)))
    df.to_parquet(PROCESSADO / "156_limpo.parquet", index=False)
    perdas.df().to_csv(SAIDAS / "tabela_perdas.csv", index=False)
    print(f"\nSalvo {len(df):,} linhas em dados/processados/156_limpo.parquet")
    estatisticas(df)


def estatisticas(df):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    df["MES"] = df["DATA_DEMANDA"].dt.to_period("M").astype(str)
    mensal = df.groupby("MES").size()
    mensal.to_csv(SAIDAS / "estat_volume_mensal.csv")
    por_ano_mes = df.groupby([df["DATA_DEMANDA"].dt.year, df["DATA_DEMANDA"].dt.month]).size().unstack(0)
    por_ano_mes.to_csv(SAIDAS / "estat_mes_x_ano.csv")
    if "BAIRRO" in df.columns:
        df["BAIRRO"].value_counts().head(20).to_csv(SAIDAS / "estat_top_bairros.csv")
    if "SERVICO_DESCRICAO" in df.columns:
        df["SERVICO_DESCRICAO"].value_counts().head(20).to_csv(SAIDAS / "estat_top_servicos.csv")
    pct = df["COORD_VALIDA"].mean() * 100
    print(f"\nVolume total: {len(df):,} | com coordenada válida: {pct:.1f}%")
    print("Volume por ano:\n", df.groupby("ANO_ARQUIVO").size())

    fig, ax = plt.subplots(figsize=(10, 4))
    mensal.plot(ax=ax, color="#888")
    ax.set_title("Volume mensal de denúncias no recorte")
    ax.set_xlabel("")
    ax.set_ylabel("denúncias")
    plt.xticks(rotation=60)
    plt.tight_layout()
    fig.savefig(SAIDAS / "volume_mensal.png", dpi=120)

    mapa_densidade(df)


def mapa_densidade(df):
    """Hexágonos de densidade sobre o contorno dos bairros.
    Só entram os pontos que caem dentro do Recife (a base limpa não é alterada)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    v = df[df["COORD_VALIDA"]]
    bairros = None
    if BAIRROS_GEOJSON.exists():
        import geopandas as gpd
        bairros = gpd.read_file(BAIRROS_GEOJSON).to_crs(4326)
        geom = bairros.geometry
        uniao = geom.union_all() if hasattr(geom, "union_all") else geom.unary_union
        pts = gpd.GeoSeries(gpd.points_from_xy(v["LON"], v["LAT"]), crs=4326)
        dentro = pts.within(uniao).to_numpy()
        print(f"Mapa: {int(dentro.sum()):,} pontos dentro do Recife; "
              f"{int((~dentro).sum()):,} fora do contorno (não entram no mapa)")
        v = v[dentro]
    else:
        print(f"AVISO: {BAIRROS_GEOJSON} não encontrado. Mapa sem contorno e sem filtro.")

    fig, ax = plt.subplots(figsize=(7, 9))
    if bairros is not None:
        bairros.plot(ax=ax, color="#efefef", edgecolor="#555555", linewidth=0.5)
    hb = ax.hexbin(v["LON"], v["LAT"], gridsize=60, bins="log", cmap="YlOrRd",
                   mincnt=1, alpha=0.9)
    fig.colorbar(hb, ax=ax, shrink=0.7, label="Número de denúncias por hexágono (escala log)")
    if bairros is not None:
        minx, miny, maxx, maxy = bairros.total_bounds
        ax.set_xlim(minx, maxx)
        ax.set_ylim(miny, maxy)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Concentração de denúncias de limpeza e drenagem no Recife")
    fig.savefig(SAIDAS / "mapa_densidade.png", dpi=150, bbox_inches="tight")


if __name__ == "__main__":
    main()
