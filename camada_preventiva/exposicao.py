"""Sprint 4: exposição simples por ponto e por bairro do piloto."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
RAW = ROOT / 'dados' / 'bruto'
PROCESSED = ROOT / 'dados' / 'processado'
OUTPUT = ROOT / 'saidas' / 'ficha_exposicao_por_ponto.csv'
CRS_M = 'EPSG:31985'
RPA_PILOTO = 4
CAMPOS_COLETA = ['SETOR_REGU', 'FREQ_REGUL', 'TURNO_REGU',
                 'SETOR_SELE', 'FREQ_SELET', 'TURNO_SELE']


def exigir(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f'Arquivo necessário não encontrado: {path}')


def ler_csv(path: Path) -> pd.DataFrame:
    exigir(path)
    for encoding in ('utf-8-sig', 'cp1252', 'latin1'):
        try:
            return pd.read_csv(path, sep=';', dtype=str, encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f'Não foi possível ler {path}.')


def normalizar_texto(valor: object) -> str:
    if pd.isna(valor):
        return ''
    texto = unicodedata.normalize('NFKD', str(valor))
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', texto.upper().strip())


def normalizar_logradouro(valor: object) -> str:
    texto = normalizar_texto(valor)
    for sigla, nome in [('AV', 'AVENIDA'), ('R', 'RUA'), ('TV', 'TRAVESSA'), ('PC', 'PRACA')]:
        texto = re.sub(rf'\b{sigla}\b', nome, texto)
    return re.sub(r'[^A-Z0-9]', '', texto)


def projetar(camada: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if camada.crs is None:
        raise ValueError('Camada sem CRS: confira a origem antes de projetar.')
    return camada.to_crs(CRS_M)


def ler_geoparquet(nome: str) -> gpd.GeoDataFrame:
    path = PROCESSED / nome
    exigir(path)
    return projetar(gpd.read_parquet(path))


def reparar_poligonos(camada: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    camada = camada.copy()
    camada.geometry = camada.geometry.make_valid()
    # make_valid pode produzir GeometryCollection com polígonos e linhas.
    for _ in range(3):
        if not camada.geom_type.isin(['MultiPolygon', 'GeometryCollection']).any():
            break
        camada = camada.explode(index_parts=False).reset_index(drop=True)
    camada = camada[camada.geom_type.eq('Polygon') & ~camada.geometry.is_empty].copy()
    if camada.empty or not camada.geometry.is_valid.all():
        raise ValueError('Não foi possível obter polígonos válidos da camada territorial.')
    return camada


def preparar_setores() -> gpd.GeoDataFrame:
    # Parte dos polígonos brutos: não remove as ilhas pequenas que cobrem pontos.
    path = RAW / 'setores_coleta.geojson'
    exigir(path)
    setores = reparar_poligonos(projetar(gpd.read_file(path)))
    for c in CAMPOS_COLETA:
        if c not in setores:
            raise ValueError(f'Campo de coleta ausente: {c}')
        setores[c] = setores[c].fillna('').astype(str).str.strip()
    # Mantém juntas as combinações de setor, frequência e turno, sem perder atributos.
    setores = setores[CAMPOS_COLETA + ['geometry']].dissolve(
        by=CAMPOS_COLETA, as_index=False, dropna=False)
    if not setores.geometry.is_valid.all():
        raise ValueError('A união dos setores gerou geometria inválida.')
    return setores


def carregar_areas() -> gpd.GeoDataFrame:
    path = RAW / 'bairros.geojson'
    exigir(path)
    bairros = projetar(gpd.read_file(path))
    bairros = bairros[bairros['CRPAAACODI'].eq(RPA_PILOTO)].copy()
    bairros = reparar_poligonos(bairros)
    bairros = bairros.dissolve(by=['CBAIRRCODI', 'EBAIRRNOME'], as_index=False)
    bairros = bairros.rename(columns={'CBAIRRCODI': 'ID_AREA', 'EBAIRRNOME': 'BAIRRO_AREA'})
    bairros['ID_AREA'] = bairros['ID_AREA'].astype('int64')
    return bairros[['ID_AREA', 'BAIRRO_AREA', 'geometry']].sort_values('ID_AREA').reset_index(drop=True)


def atribuir_areas(registros: gpd.GeoDataFrame, areas: gpd.GeoDataFrame,
                   id_col: str) -> pd.DataFrame:
    if registros[id_col].isna().any() or registros[id_col].duplicated().any():
        raise ValueError(f'{id_col} deve ser único e preenchido.')
    j = gpd.sjoin(projetar(registros)[[id_col, 'geometry']], areas[['ID_AREA', 'geometry']],
                  how='left', predicate='intersects')
    # Na fronteira de dois bairros, escolhe o menor código oficial; nunca duplica a contagem.
    return j.sort_values('ID_AREA').drop_duplicates(id_col)[[id_col, 'ID_AREA']]


def cruzar_drenagem(pontos: gpd.GeoDataFrame) -> pd.DataFrame:
    pontos = projetar(pontos)
    elementos = ler_geoparquet('drenagem_elementos.parquet')
    elementos = elementos[elementos.geometry.notna() & ~elementos.geometry.is_empty].copy()
    index = pd.Index(pontos['ID_PONTO'], name='ID_PONTO')
    if elementos.empty:
        return pd.DataFrame({'DISTANCIA_DRENAGEM_M': np.nan, 'TIPO_DRENAGEM_PROXIMA': 'Sem dado',
                             'DRENAGEM_ATE_100M': 'Sem dado'}, index=index)
    j = gpd.sjoin_nearest(pontos[['ID_PONTO', 'geometry']], elementos[['TIPO', 'geometry']],
                          how='left', distance_col='DISTANCIA_DRENAGEM_M')
    resultado = j.groupby('ID_PONTO').agg(
        DISTANCIA_DRENAGEM_M=('DISTANCIA_DRENAGEM_M', 'min'),
        TIPO_DRENAGEM_PROXIMA=('TIPO', lambda s: ', '.join(sorted(set(s.dropna().astype(str))))))
    resultado['DRENAGEM_ATE_100M'] = resultado.DISTANCIA_DRENAGEM_M.le(100 + 1e-6).map({True: 'Sim', False: 'Não'})
    resultado.loc[resultado.DISTANCIA_DRENAGEM_M.isna(), 'DRENAGEM_ATE_100M'] = 'Sem dado'
    return resultado


def cruzar_setores_coleta(pontos: gpd.GeoDataFrame,
                          setores: gpd.GeoDataFrame | None = None) -> pd.DataFrame:
    setores = preparar_setores() if setores is None else projetar(setores)
    j = gpd.sjoin(projetar(pontos)[['ID_PONTO', 'geometry']], setores[CAMPOS_COLETA + ['geometry']],
                  how='left', predicate='intersects')
    resultados = []
    for pid, grupo in j.groupby('ID_PONTO'):
        combos = grupo.loc[grupo.index_right.notna(), CAMPOS_COLETA].fillna('').drop_duplicates()
        row = {'ID_PONTO': pid, 'STATUS_COLETA': 'SEM_COBERTURA_MAPEADA',
               'COLETA_NO_SETOR': 'Sem cobertura mapeada'}
        row.update({c: '' for c in CAMPOS_COLETA})
        if len(combos) == 1:
            row.update(combos.iloc[0].to_dict())
            row['STATUS_COLETA'] = 'UNICA'
        elif len(combos) > 1:
            row['STATUS_COLETA'] = 'AMBIGUA'
        partes = []
        for _, combo in combos.iterrows():
            for titulo, campos in [('Regular', CAMPOS_COLETA[:3]), ('Seletiva', CAMPOS_COLETA[3:])]:
                valores = [str(combo[c]).strip() for c in campos if str(combo[c]).strip()]
                if valores:
                    partes.append(f'{titulo}: {" / ".join(valores)}')
        if partes:
            row['COLETA_NO_SETOR'] = ' | '.join(sorted(set(partes)))
        resultados.append(row)
    return pd.DataFrame(resultados).set_index('ID_PONTO')


def cruzar_roteiro(pontos: gpd.GeoDataFrame) -> pd.DataFrame:
    """Nome da rua fornece candidatos; não comprova qual trecho/rota atende ao ponto."""
    path = PROCESSED / 'denuncias_pontos.parquet'
    exigir(path)
    denuncias = pd.read_parquet(path)
    rotas = ler_csv(RAW / 'roteiros_coleta.csv')
    rotas.columns = rotas.columns.str.strip().str.lower()
    campos = ['setor', 'rota_setor', 'frequencia', 'turno']
    if not set(campos + ['endereco']).issubset(rotas.columns):
        raise ValueError('Roteiro sem os campos esperados.')
    rotas['_logradouro'] = rotas.endereco.map(normalizar_logradouro)
    rotas = rotas[rotas._logradouro.ne('')]
    d = denuncias[['ID_PONTO', 'LOGRADOURO']].drop_duplicates().copy()
    d['_logradouro'] = d.LOGRADOURO.map(normalizar_logradouro)
    j = d[d._logradouro.ne('')].merge(rotas, on='_logradouro', how='left')
    registros = []
    for pid in pontos.ID_PONTO:
        grupo = j[j.ID_PONTO.eq(pid)].dropna(subset=['setor', 'frequencia', 'turno'])
        variantes = grupo[campos].fillna('').drop_duplicates()
        setores = variantes.setor.nunique()
        status = 'SEM_CORRESPONDENCIA'
        if len(variantes) == 1:
            status = 'CANDIDATO_TEXTUAL'
        elif len(variantes) > 1:
            status = 'AMBIGUO'
        textos = [' | '.join(str(v).strip() for v in row if str(v).strip())
                  for row in variantes.itertuples(index=False, name=None)]
        registros.append({'ID_PONTO': pid, 'STATUS_ROTEIRO': status,
                          'N_SETORES_ROTEIRO_CANDIDATOS': setores,
                          'ROTEIRO_DE_COLETA': '; '.join(sorted(textos)) or 'Sem correspondência por logradouro'})
    return pd.DataFrame(registros).set_index('ID_PONTO')


def exposicao_areas(areas: gpd.GeoDataFrame, setores: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    elementos = ler_geoparquet('drenagem_elementos.parquet')
    elementos = elementos[elementos.geometry.notna() & ~elementos.geometry.is_empty].reset_index(drop=True)
    elementos['ID_ELEMENTO'] = elementos.index
    vinculo = atribuir_areas(elementos, areas, 'ID_ELEMENTO')
    resultado = areas.copy()
    resultado['AREA_KM2'] = resultado.area / 1e6
    resultado['N_ELEMENTOS_DRENAGEM'] = resultado.ID_AREA.map(vinculo.ID_AREA.value_counts()).fillna(0).astype(int)
    resultado['DENSIDADE_DRENAGEM_KM2'] = resultado.N_ELEMENTOS_DRENAGEM / resultado.AREA_KM2
    # Só usa polígonos com cadastro regular preenchido; ausência significa falta de mapa, não de serviço.
    cobertura = setores[setores[['SETOR_REGU', 'FREQ_REGUL', 'TURNO_REGU']].ne('').all(axis=1)].geometry.union_all()
    resultado['COBERTURA_COLETA'] = (resultado.geometry.intersection(cobertura).area / resultado.area).clip(0, 1)
    resultado['DRENAGEM_DISPONIVEL'] = not elementos.empty
    return resultado


def main() -> None:
    pontos = ler_geoparquet('pontos_agrupados.parquet')
    if pontos.ID_PONTO.isna().any() or pontos.ID_PONTO.duplicated().any():
        raise ValueError('ID_PONTO deve ser único e preenchido.')
    setores = preparar_setores()
    areas = carregar_areas()
    ficha = pontos.set_index('ID_PONTO')[[
        c for c in ['ID_LINHA_REFERENCIA', 'N_DENUNCIAS', 'SITUACAO_PONTO', 'LAT', 'LON'] if c in pontos]].copy()
    ficha = ficha.join(cruzar_drenagem(pontos)).join(cruzar_setores_coleta(pontos, setores)).join(cruzar_roteiro(pontos))
    vinculo = atribuir_areas(pontos, areas, 'ID_PONTO').set_index('ID_PONTO')
    ficha = ficha.join(vinculo).reset_index()
    if len(ficha) != len(pontos) or ficha.ID_PONTO.duplicated().any():
        raise ValueError('A ficha deve preservar exatamente uma linha por ponto.')
    area_exposicao = exposicao_areas(areas, setores)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    setores.to_parquet(PROCESSED / 'setores_coleta.parquet', index=False)
    area_exposicao.to_parquet(PROCESSED / 'exposicao_areas.parquet', index=False)
    area_exposicao.drop(columns='geometry').to_csv(ROOT / 'saidas/exposicao_areas.csv', index=False, encoding='utf-8-sig')
    ficha.to_csv(OUTPUT, index=False, encoding='utf-8-sig')
    print(f'Ficha gerada: {OUTPUT} ({len(ficha)} pontos)')
    print(f'Sem setor mapeado: {ficha.STATUS_COLETA.eq("SEM_COBERTURA_MAPEADA").sum()}')
    print(f'Roteiro: {ficha.STATUS_ROTEIRO.value_counts().to_dict()}')
    print(f'Exposição por área: {len(area_exposicao)} bairros oficiais da RPA {RPA_PILOTO}')


if __name__ == '__main__':
    main()
