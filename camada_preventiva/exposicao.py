

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import geopandas as gpd
import pandas as pd


ROOT = Path(__file__).resolve().parent
RAW = ROOT / "dados" / "bruto"
PROCESSED = ROOT / "dados" / "processado"
OUTPUT = ROOT / "saidas" / "ficha_exposicao_por_ponto.csv"


def exigir(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Arquivo necessário não encontrado: {path}")


def ler_csv(path: Path) -> pd.DataFrame:
    """Lê o CSV de roteiros, preservando acentos e os campos como texto."""
    for encoding in ("utf-8-sig", "cp1252", "latin1"):
        try:
            return pd.read_csv(path, sep=";", dtype=str, encoding=encoding)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, sep=";", dtype=str, encoding="latin1")


def normalizar_logradouro(valor: object) -> str:
    if pd.isna(valor):
        return ""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(char for char in texto if not unicodedata.combining(char)).upper()
    texto = re.sub(r"\bAV\.?\b", " AVENIDA ", texto)
    texto = re.sub(r"\bR\.?\b", " RUA ", texto)
    texto = re.sub(r"\bTV\.?\b", " TRAVESSA ", texto)
    texto = re.sub(r"\bPC\.?\b", " PRACA ", texto)
    return re.sub(r"[^A-Z0-9]", "", texto)


def ler_geoparquet(nome: str) -> gpd.GeoDataFrame:
    path = PROCESSED / nome
    exigir(path)
    camada = gpd.read_parquet(path)
    if camada.crs is None:
        raise ValueError(f"{path.name} não tem CRS definido.")
    return camada


def cruzar_drenagem(pontos: gpd.GeoDataFrame) -> pd.DataFrame:
    elementos = ler_geoparquet("drenagem_elementos.parquet")
    if elementos.crs != pontos.crs:
        elementos = elementos.to_crs(pontos.crs)
    elementos = elementos.loc[
        elementos.geometry.notna() & ~elementos.geometry.is_empty
    ].copy()
    if elementos.empty:
        return pd.DataFrame({
            "DISTANCIA_DRENAGEM_M": float("nan"),
            "TIPO_DRENAGEM_PROXIMA": "Sem dado",
            "DRENAGEM_ATE_100M": "Sem dado",
        }, index=pd.Index(pontos["ID_PONTO"], name="ID_PONTO"))

    tipo_col = "TIPO" if "TIPO" in elementos.columns else None
    distancias = gpd.sjoin_nearest(
        pontos[["ID_PONTO", "geometry"]],
        elementos[[tipo_col, "geometry"]] if tipo_col else elementos[["geometry"]],
        how="left",
        distance_col="DISTANCIA_DRENAGEM_M",
    )
    minimas = distancias.groupby("ID_PONTO")["DISTANCIA_DRENAGEM_M"].min()
    resultado = minimas.to_frame()
    if tipo_col:
        mais_proximos = distancias.join(minimas.rename("_minima"), on="ID_PONTO")
        mais_proximos = mais_proximos[
            (mais_proximos["DISTANCIA_DRENAGEM_M"] - mais_proximos["_minima"]).abs() < 0.01
        ]
        tipos = mais_proximos.groupby("ID_PONTO")[tipo_col].agg(
            lambda valores: ", ".join(
                sorted({str(valor).strip() for valor in valores.dropna() if str(valor).strip()})
            ) or "Não informado"
        )
        resultado["TIPO_DRENAGEM_PROXIMA"] = tipos
    resultado["DRENAGEM_ATE_100M"] = resultado["DISTANCIA_DRENAGEM_M"].le(100).map(
        {True: "Sim", False: "Não"}
    )
    resultado.loc[resultado["DISTANCIA_DRENAGEM_M"].isna(), "DRENAGEM_ATE_100M"] = "Sem dado"
    return resultado


def cruzar_setores_coleta(pontos: gpd.GeoDataFrame) -> pd.DataFrame:
    setores = ler_geoparquet("setores_coleta.parquet")
    if setores.crs != pontos.crs:
        setores = setores.to_crs(pontos.crs)
    campos = [
        campo for campo in (
            "SETOR_REGU", "FREQ_REGUL", "TURNO_REGU",
            "SETOR_SELE", "FREQ_SELET", "TURNO_SELE",
        ) if campo in setores.columns
    ]
    if not campos:
        raise ValueError("setores_coleta.parquet não contém os campos de frequência/turno esperados.")
    setores = setores[campos + ["geometry"]]
    cruzamento = gpd.sjoin(
        pontos[["ID_PONTO", "geometry"]], setores, how="left", predicate="intersects"
    )

    def resumir(valores: pd.Series) -> str:
        unicos = sorted({str(v).strip() for v in valores.dropna() if str(v).strip()})
        return ", ".join(unicos) if unicos else "Sem cobertura mapeada"

    resumo = cruzamento.groupby("ID_PONTO").agg(
        **{f"_{campo}": (campo, resumir) for campo in campos}
    )

    def montar_coleta(linha: pd.Series) -> str:
        partes = []
        for titulo, setor, frequencia, turno in (
            ("Regular", "_SETOR_REGU", "_FREQ_REGUL", "_TURNO_REGU"),
            ("Seletiva", "_SETOR_SELE", "_FREQ_SELET", "_TURNO_SELET"),
        ):
            dados = [linha[c] for c in (setor, frequencia, turno) if c in linha.index]
            dados = [v for v in dados if v and v != "Sem cobertura mapeada"]
            if dados:
                partes.append(f"{titulo}: {' / '.join(dados)}")
        return " | ".join(partes) if partes else "Sem cobertura mapeada"

    resumo["COLETA_NO_SETOR"] = resumo.apply(montar_coleta, axis=1)
    return resumo[["COLETA_NO_SETOR"]]


def cruzar_roteiro(pontos: gpd.GeoDataFrame) -> pd.DataFrame:
    """Cruza o roteiro por logradouro das denúncias que compõem cada ponto."""
    denuncias_path = PROCESSED / "denuncias_pontos.parquet"
    roteiro_path = RAW / "roteiros_coleta.csv"
    exigir(denuncias_path)
    exigir(roteiro_path)
    denuncias = pd.read_parquet(denuncias_path)
    if not {"ID_PONTO", "LOGRADOURO"}.issubset(denuncias.columns):
        raise ValueError("denuncias_pontos.parquet precisa conter ID_PONTO e LOGRADOURO.")
    rotas = ler_csv(roteiro_path)
    rotas.columns = [str(c).strip().lower() for c in rotas.columns]
    necessarias = {"endereco", "setor", "rota_setor", "frequencia", "turno"}
    faltantes = necessarias - set(rotas.columns)
    if faltantes:
        raise ValueError(f"roteiros_coleta.csv sem colunas esperadas: {sorted(faltantes)}")

    rotas["_logradouro"] = rotas["endereco"].map(normalizar_logradouro)
    rotas = rotas.loc[rotas["_logradouro"].ne("")].copy()
    denuncias = denuncias[["ID_PONTO", "LOGRADOURO"]].dropna(subset=["LOGRADOURO"]).copy()
    denuncias["_logradouro"] = denuncias["LOGRADOURO"].map(normalizar_logradouro)
    cruzamento = denuncias.merge(rotas, on="_logradouro", how="left")

    def resumir(grupo: pd.DataFrame) -> str:
        correspondencias = grupo.dropna(subset=["setor", "frequencia", "turno"])
        valores = {
            " | ".join(str(v).strip() for v in (linha.setor, linha.rota_setor, linha.frequencia, linha.turno)
                       if pd.notna(v) and str(v).strip())
            for linha in correspondencias.itertuples(index=False)
        }
        valores.discard("")
        return "; ".join(sorted(valores)) if valores else "Sem correspondência por logradouro"

    roteiro = cruzamento.groupby("ID_PONTO").apply(resumir).rename("ROTEIRO_DE_COLETA")
    return roteiro.reindex(pontos["ID_PONTO"]).to_frame().fillna(
        "Sem correspondência por logradouro"
    ).set_axis(pontos["ID_PONTO"], axis="index")


def main() -> None:
    pontos = ler_geoparquet("pontos_agrupados.parquet")
    campos_obrigatorios = {"ID_PONTO", "N_DENUNCIAS", "geometry"}
    if not campos_obrigatorios.issubset(pontos.columns):
        raise ValueError(f"pontos_agrupados.parquet precisa conter {sorted(campos_obrigatorios)}.")
    if pontos["ID_PONTO"].duplicated().any():
        raise ValueError("A base da Sprint 3 contém ID_PONTO duplicado.")

    exposicao = pontos.set_index("ID_PONTO")[[
        c for c in ("ID_LINHA_REFERENCIA", "N_DENUNCIAS", "SITUACAO_PONTO", "LAT", "LON")
        if c in pontos.columns
    ]].copy()
    exposicao = exposicao.join(cruzar_drenagem(pontos)).join(cruzar_setores_coleta(pontos))
    exposicao = exposicao.join(cruzar_roteiro(pontos))
    exposicao = exposicao.reset_index()
    if "DISTANCIA_DRENAGEM_M" in exposicao:
        exposicao["DISTANCIA_DRENAGEM_M"] = exposicao["DISTANCIA_DRENAGEM_M"].round(1)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    exposicao.to_csv(OUTPUT, index=False, encoding="utf-8-sig")
    print(f"Ficha gerada: {OUTPUT}")
    print(f"Pontos da Sprint 3 preservados: {len(exposicao):,}")
    print(f"Pontos com drenagem a até 100 m: {(exposicao['DRENAGEM_ATE_100M'] == 'Sim').sum():,}")
    print(f"Pontos sem cruzamento de logradouro no roteiro: "
          f"{exposicao['ROTEIRO_DE_COLETA'].eq('Sem correspondência por logradouro').sum():,}")


if __name__ == "__main__":
    main()
