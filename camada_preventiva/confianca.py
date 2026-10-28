"""Sprint 5: referência simples de denúncias por bairro, sem modelo preditivo."""
from __future__ import annotations

import numpy as np
import pandas as pd
import geopandas as gpd

if __package__:
    from . import exposicao
else:
    import exposicao

ROOT = exposicao.ROOT
PROCESSED = exposicao.PROCESSED
OUTPUT = ROOT / 'saidas'
MIN_COBERTURA_COORD = 0.50
MIN_COBERTURA_COLETA = 0.80


def contar_observado(areas: gpd.GeoDataFrame, base: pd.DataFrame,
                    denuncias: pd.DataFrame) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    elegiveis = base[base.COORD_VALIDA.eq(True)]
    if (denuncias.ID_LINHA.isna().any() or denuncias.ID_LINHA.duplicated().any() or
            set(denuncias.ID_LINHA) != set(elegiveis.ID_LINHA)):
        raise ValueError('O histórico agrupado não corresponde à base limpa. Refaça as sprints anteriores.')
    origem = elegiveis.set_index('ID_LINHA').loc[denuncias.ID_LINHA, ['X', 'Y']].to_numpy(dtype=float)
    if not np.allclose(origem, denuncias[['X', 'Y']].to_numpy(dtype=float), rtol=0, atol=1e-6):
        raise ValueError('As coordenadas do histórico diferem da base limpa. Refaça o agrupamento.')
    if not np.isfinite(denuncias[['X', 'Y']].to_numpy(dtype=float)).all():
        raise ValueError('Há coordenadas inválidas no histórico das denúncias.')
    registros = gpd.GeoDataFrame(denuncias[['ID_LINHA']].copy(),
        geometry=gpd.points_from_xy(denuncias.X, denuncias.Y), crs=exposicao.CRS_M)
    vinculo = exposicao.atribuir_areas(registros, areas, 'ID_LINHA')
    resultado = areas.copy()
    resultado['OBSERVADO'] = resultado.ID_AREA.map(vinculo.ID_AREA.value_counts()).fillna(0).astype(int)
    # A cobertura é um diagnóstico por bairro INFORMADO no 156; não redistribui os registros sem coordenada.
    b = base.assign(_bairro=base.BAIRRO.map(exposicao.normalizar_texto))
    cobertura = b.groupby('_bairro').COORD_VALIDA.mean()
    resultado['COBERTURA_COORD_INFORMADA'] = resultado.BAIRRO_AREA.map(
        exposicao.normalizar_texto).map(cobertura).fillna(0)
    assert int(resultado.OBSERVADO.sum()) == int(vinculo.ID_AREA.notna().sum())
    return resultado, vinculo


def classificar_areas(areas: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    resultado = areas.copy()
    if resultado.empty:
        raise ValueError('Não há áreas de exposição para comparar.')
    for c in ['AREA_KM2', 'DENSIDADE_DRENAGEM_KM2', 'OBSERVADO',
              'COBERTURA_COLETA', 'COBERTURA_COORD_INFORMADA']:
        if not np.isfinite(resultado[c]).all() or resultado[c].lt(0).any():
            raise ValueError(f'Variável de área ausente ou inválida: {c}')
    if resultado.AREA_KM2.le(0).any() or resultado.ID_AREA.duplicated().any():
        raise ValueError('As áreas devem ser positivas e identificadas uma única vez.')
    if resultado[['COBERTURA_COLETA', 'COBERTURA_COORD_INFORMADA']].gt(1).any().any():
        raise ValueError('Cobertura deve estar entre 0 e 1.')
    # Referência relativa: área territorial, ajustada pela densidade de infraestrutura (fator 1 a 2).
    # Proximidade/densidade não prova risco de alagamento; esta é uma heurística explícita do piloto.
    maxima = resultado.DENSIDADE_DRENAGEM_KM2.max()
    densidade_relativa = resultado.DENSIDADE_DRENAGEM_KM2 / maxima if maxima > 0 else 0
    resultado['PESO_EXPOSICAO'] = resultado.AREA_KM2 * (1 + densidade_relativa)
    total = resultado.OBSERVADO.sum()
    resultado['ESPERADO'] = total * resultado.PESO_EXPOSICAO / resultado.PESO_EXPOSICAO.sum()
    resultado['DIFERENCA'] = resultado.OBSERVADO - resultado.ESPERADO
    resultado['RAZAO_OBSERVADO_ESPERADO'] = resultado.OBSERVADO / resultado.ESPERADO.replace(0, np.nan)
    resultado['CLASSIFICACAO'] = 'A_VERIFICAR'
    resultado['TIPO_SINAL'] = 'DEFICIT_POSSIVEL_SILENCIO'
    resultado.loc[resultado.OBSERVADO.ge(resultado.ESPERADO), 'TIPO_SINAL'] = 'OBSERVADO_IGUAL_OU_ACIMA'
    motivos = []
    for i, area in resultado.iterrows():
        pendencias = []
        if total == 0:
            pendencias.append('Sem denúncias georreferenciadas no conjunto: referência indisponível')
        if not area.DRENAGEM_DISPONIVEL:
            pendencias.append('Base de drenagem sem elementos disponíveis')
        if area.COBERTURA_COORD_INFORMADA < MIN_COBERTURA_COORD:
            pendencias.append('Menos de 50% dos registros do bairro informado têm coordenadas válidas')
        if area.COBERTURA_COLETA < MIN_COBERTURA_COLETA:
            pendencias.append('Menos de 80% da área tem cobertura de coleta mapeada')
        if not pendencias and area.OBSERVADO > 0 and area.OBSERVADO >= area.ESPERADO:
            resultado.at[i, 'CLASSIFICACAO'] = 'CONFIRMADO'
            motivos.append('Observado igual ou acima da referência relativa de exposição')
        else:
            if area.OBSERVADO < area.ESPERADO:
                pendencias.append('Observado abaixo da referência: verificar possível silêncio, cobertura e cadastro')
            motivos.append('; '.join(pendencias) or 'Referência insuficiente para confirmar o sinal')
    resultado['JUSTIFICATIVA'] = motivos
    return resultado


def vincular_pontos(pontos: gpd.GeoDataFrame, areas: gpd.GeoDataFrame) -> pd.DataFrame:
    vinculo = exposicao.atribuir_areas(pontos, areas, 'ID_PONTO')
    campos = ['ID_AREA', 'BAIRRO_AREA', 'OBSERVADO', 'ESPERADO', 'RAZAO_OBSERVADO_ESPERADO',
              'CLASSIFICACAO', 'TIPO_SINAL', 'JUSTIFICATIVA']
    ficha = pontos[['ID_PONTO', 'N_DENUNCIAS']].merge(vinculo, on='ID_PONTO', validate='one_to_one')
    ficha = ficha.merge(areas[campos], on='ID_AREA', how='left', validate='many_to_one')
    fora = ficha.ID_AREA.isna()
    ficha.loc[fora, 'CLASSIFICACAO'] = 'A_VERIFICAR'
    ficha.loc[fora, 'TIPO_SINAL'] = 'TERRITORIO_PENDENTE'
    ficha.loc[fora, 'JUSTIFICATIVA'] = 'Centro fora dos bairros oficiais da RPA piloto; conferir a coordenada/RPA informada'
    return ficha.rename(columns={'OBSERVADO': 'OBSERVADO_AREA', 'ESPERADO': 'ESPERADO_AREA'})


def main() -> None:
    path = PROCESSED / 'exposicao_areas.parquet'
    exposicao.exigir(path)
    areas = exposicao.projetar(gpd.read_parquet(path))
    pontos = exposicao.ler_geoparquet('pontos_agrupados.parquet')
    base = pd.read_parquet(PROCESSED / '156_limpo.parquet')
    denuncias = pd.read_parquet(PROCESSED / 'denuncias_pontos.parquet')
    if not base.GRUPOSERVICO_DESCRICAO.eq('DRENAGEM').all() or not base.RPA_NUM.eq(exposicao.RPA_PILOTO).all():
        raise ValueError('Esta versão usa Drenagem na RPA 4. Confira o recorte antes de executar.')
    contagens = denuncias.groupby('ID_PONTO').size().sort_index()
    if (pontos.ID_PONTO.isna().any() or pontos.ID_PONTO.duplicated().any() or
            contagens.to_dict() != pontos.set_index('ID_PONTO').N_DENUNCIAS.to_dict()):
        raise ValueError('Pontos e histórico têm contagens ou IDs diferentes. Refaça a sprint 3.')
    areas, vinculo = contar_observado(areas, base, denuncias)
    areas = classificar_areas(areas)
    ficha = vincular_pontos(pontos, areas)
    inicio, fim = base.DATA_DEMANDA.min(), base.DATA_DEMANDA.max()
    areas['PERIODO_INICIO'] = inicio
    areas['PERIODO_FIM'] = fim
    fora = vinculo[vinculo.ID_AREA.isna()].copy()
    fora['MOTIVO'] = 'Coordenada fora dos bairros oficiais da RPA 4'
    diagnostico = pd.DataFrame([
        {'INDICADOR': 'Registros na base limpa', 'QUANTIDADE': len(base)},
        {'INDICADOR': 'Registros sem coordenadas válidas', 'QUANTIDADE': int((~base.COORD_VALIDA).sum())},
        {'INDICADOR': 'Georreferenciados fora da RPA geométrica', 'QUANTIDADE': len(fora)},
        {'INDICADOR': 'Observados usados na comparação', 'QUANTIDADE': int(areas.OBSERVADO.sum())},
        {'INDICADOR': 'Áreas incluídas, inclusive com zero denúncias', 'QUANTIDADE': len(areas)},
    ])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    areas.to_parquet(PROCESSED / 'confianca_areas.parquet', index=False)
    areas.drop(columns='geometry').to_csv(OUTPUT / 'confianca_por_area.csv', index=False, encoding='utf-8-sig')
    ficha.to_csv(OUTPUT / 'confianca_por_ponto.csv', index=False, encoding='utf-8-sig')
    diagnostico.to_csv(OUTPUT / 'diagnostico_confianca.csv', index=False, encoding='utf-8-sig')
    fora.to_csv(OUTPUT / 'denuncias_fora_area.csv', index=False, encoding='utf-8-sig')
    print(f'Período observado: {inicio.date()} a {fim.date()} (não é previsão para 2026)')
    print(diagnostico.to_string(index=False))
    print(f'Classificações por área: {areas.CLASSIFICACAO.value_counts().to_dict()}')
    print(f'Pontos preservados: {len(ficha)}; fora da RPA geométrica: {ficha.ID_AREA.isna().sum()}')


if __name__ == '__main__':
    main()
