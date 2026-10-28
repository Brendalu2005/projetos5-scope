"""Validação das correções de exposição e da referência simples por área."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Polygon, box, MultiPolygon

from camada_preventiva import exposicao as exp
from camada_preventiva import confianca as conf


def pontos(xs=(5,), ys=None):
    ys = [5] * len(xs) if ys is None else ys
    return gpd.GeoDataFrame({'ID_PONTO': range(1, len(xs) + 1), 'N_DENUNCIAS': [1] * len(xs)},
        geometry=gpd.points_from_xy(xs, ys), crs=exp.CRS_M)


def areas_teste():
    return gpd.GeoDataFrame({'ID_AREA': [1, 2, 3], 'BAIRRO_AREA': ['A', 'B', 'C'],
        'AREA_KM2': [1., 1., 1.], 'DENSIDADE_DRENAGEM_KM2': [10., 10., 10.],
        'COBERTURA_COLETA': [1., 1., 1.], 'DRENAGEM_DISPONIVEL': [True] * 3,
        'COBERTURA_COORD_INFORMADA': [1., 1., 1.], 'OBSERVADO': [9, 0, 3]},
        geometry=[box(0, 0, 10, 10), box(10, 0, 20, 10), box(20, 0, 30, 10)], crs=exp.CRS_M)


class ExposicaoTest(unittest.TestCase):
    def test_reparo_preserva_ilha_pequena(self):
        g = gpd.GeoDataFrame({'id': [1, 2]}, geometry=[
            MultiPolygon([box(0, 0, 100, 100), box(200, 200, 201, 201)]),
            Polygon([(300, 0), (302, 2), (300, 2), (302, 0), (300, 0)])], crs=exp.CRS_M)
        r = exp.reparar_poligonos(g)
        self.assertTrue(r.is_valid.all())
        self.assertTrue(r.intersects(box(200.1, 200.1, 200.2, 200.2)).any())
        self.assertTrue((r.area < 500).any())

    def test_turno_seletivo_e_campos_separados_sao_preservados(self):
        s = gpd.GeoDataFrame({'SETOR_REGU': ['R1'], 'FREQ_REGUL': ['DIARIA'],
            'TURNO_REGU': ['DIURNO'], 'SETOR_SELE': ['S1'], 'FREQ_SELET': ['SEGUNDA'],
            'TURNO_SELE': ['NOTURNO']}, geometry=[box(0, 0, 10, 10)], crs=exp.CRS_M)
        r = exp.cruzar_setores_coleta(pontos(), s).iloc[0]
        self.assertEqual(r.TURNO_SELE, 'NOTURNO')
        self.assertIn('Seletiva: S1 / SEGUNDA / NOTURNO', r.COLETA_NO_SETOR)
        self.assertEqual(r.STATUS_COLETA, 'UNICA')

    def test_sobreposicao_nao_inventa_setor_unico(self):
        s = gpd.GeoDataFrame({c: ['A', 'B'] for c in exp.CAMPOS_COLETA},
            geometry=[box(0, 0, 10, 10)] * 2, crs=exp.CRS_M)
        r = exp.cruzar_setores_coleta(pontos(), s).iloc[0]
        self.assertEqual(r.STATUS_COLETA, 'AMBIGUA')
        self.assertEqual(r.SETOR_REGU, '')

    def test_falta_de_cobertura_nao_significa_falta_de_servico(self):
        s = gpd.GeoDataFrame({c: ['A'] for c in exp.CAMPOS_COLETA},
            geometry=[box(50, 50, 60, 60)], crs=exp.CRS_M)
        r = exp.cruzar_setores_coleta(pontos(), s).iloc[0]
        self.assertEqual(r.STATUS_COLETA, 'SEM_COBERTURA_MAPEADA')
        self.assertEqual(r.FREQ_REGUL, '')

    def test_drenagem_distancia_em_metros_e_limite_100(self):
        d = gpd.GeoDataFrame({'TIPO': ['PV']}, geometry=gpd.points_from_xy([0], [0]), crs=exp.CRS_M)
        p = pontos([100, 100.01], [0, 0]).to_crs(4326)
        with patch.object(exp, 'ler_geoparquet', return_value=d):
            r = exp.cruzar_drenagem(p)
        self.assertAlmostEqual(r.loc[1, 'DISTANCIA_DRENAGEM_M'], 100., places=5)
        self.assertEqual(r.loc[1, 'DRENAGEM_ATE_100M'], 'Sim')
        self.assertEqual(r.loc[2, 'DRENAGEM_ATE_100M'], 'Não')
        self.assertEqual(r.loc[1, 'TIPO_DRENAGEM_PROXIMA'], 'PV')

    def test_roteiros_sao_candidatos_e_ambiguidades_ficam_visiveis(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            pd.DataFrame({'ID_PONTO': [1, 2, 3], 'LOGRADOURO': ['RUA TESTE', 'RUA UNICA', 'RUA AUSENTE']}).to_parquet(path / 'denuncias_pontos.parquet')
            pd.DataFrame({'endereco': ['R TESTE', 'R TESTE', 'R UNICA'], 'setor': ['A', 'B', 'C'],
                'rota_setor': ['1', '2', '3'], 'frequencia': ['DIARIA'] * 3, 'turno': ['DIURNO'] * 3}).to_csv(path / 'roteiros_coleta.csv', sep=';', index=False)
            with patch.object(exp, 'RAW', path), patch.object(exp, 'PROCESSED', path):
                r = exp.cruzar_roteiro(pontos([1, 2, 3]))
        self.assertEqual(r.STATUS_ROTEIRO.tolist(), ['AMBIGUO', 'CANDIDATO_TEXTUAL', 'SEM_CORRESPONDENCIA'])
        self.assertEqual(r.loc[1, 'N_SETORES_ROTEIRO_CANDIDATOS'], 2)
        self.assertTrue(r.index.is_unique)

    def test_borda_entre_areas_nao_duplica_registro(self):
        r = exp.atribuir_areas(pontos([10], [5]), areas_teste(), 'ID_PONTO')
        self.assertEqual(len(r), 1)
        self.assertEqual(r.iloc[0].ID_AREA, 1)


class ConfiancaTest(unittest.TestCase):
    def test_area_sem_denuncias_participa_e_recebe_deficit(self):
        r = conf.classificar_areas(areas_teste())
        self.assertEqual(len(r), 3)
        self.assertAlmostEqual(r.ESPERADO.sum(), r.OBSERVADO.sum())
        self.assertAlmostEqual(r.loc[1, 'ESPERADO'], 4.)
        self.assertEqual(r.loc[1, 'CLASSIFICACAO'], 'A_VERIFICAR')
        self.assertEqual(r.loc[1, 'TIPO_SINAL'], 'DEFICIT_POSSIVEL_SILENCIO')
        self.assertEqual(r.loc[0, 'CLASSIFICACAO'], 'CONFIRMADO')

    def test_dados_insuficientes_nao_confirmam_excesso(self):
        a = areas_teste();a.loc[0, 'COBERTURA_COORD_INFORMADA'] = .2
        r = conf.classificar_areas(a)
        self.assertEqual(r.loc[0, 'CLASSIFICACAO'], 'A_VERIFICAR')
        self.assertIn('50%', r.loc[0, 'JUSTIFICATIVA'])
        a = areas_teste();a.loc[0, 'COBERTURA_COLETA'] = .2
        self.assertEqual(conf.classificar_areas(a).loc[0, 'CLASSIFICACAO'], 'A_VERIFICAR')

    def test_sem_observacao_nao_divide_zero_nem_confirma(self):
        a = areas_teste();a['OBSERVADO'] = 0;a['DENSIDADE_DRENAGEM_KM2'] = 0
        r = conf.classificar_areas(a)
        self.assertTrue(r.ESPERADO.eq(0).all())
        self.assertTrue(r.RAZAO_OBSERVADO_ESPERADO.isna().all())
        self.assertTrue(r.CLASSIFICACAO.eq('A_VERIFICAR').all())

    def test_drenagem_indisponivel_nao_confirma(self):
        a = areas_teste();a['DRENAGEM_DISPONIVEL'] = False
        self.assertTrue(conf.classificar_areas(a).CLASSIFICACAO.eq('A_VERIFICAR').all())

    def test_individuos_de_um_grupo_podem_pertencer_a_bairros_diferentes(self):
        h = pd.DataFrame({'ID_LINHA': [1, 2, 3], 'ID_PONTO': [1, 1, 2],
            'X': [5., 15., 50.], 'Y': [5., 5., 5.]})
        base = h.assign(COORD_VALIDA=True, BAIRRO=['A', 'B', 'C'])
        a, v = conf.contar_observado(areas_teste(), base, h)
        self.assertEqual(a.OBSERVADO.tolist(), [1, 1, 0])
        self.assertEqual(int(v.ID_AREA.isna().sum()), 1)

    def test_historico_desatualizado_e_bloqueado(self):
        h = pd.DataFrame({'ID_LINHA': [1], 'X': [5.], 'Y': [5.]})
        base = h.assign(COORD_VALIDA=True, BAIRRO='A');h.loc[0, 'X'] = 15
        with self.assertRaisesRegex(ValueError, 'coordenadas'):
            conf.contar_observado(areas_teste(), base, h)

    def test_ponto_fora_do_piloto_e_preservado_para_verificacao(self):
        a = conf.classificar_areas(areas_teste())
        r = conf.vincular_pontos(pontos([5, 50]), a)
        self.assertEqual(len(r), 2)
        self.assertEqual(r.iloc[1].CLASSIFICACAO, 'A_VERIFICAR')
        self.assertEqual(r.iloc[1].TIPO_SINAL, 'TERRITORIO_PENDENTE')
        self.assertTrue(pd.isna(r.iloc[1].ESPERADO_AREA))

    def test_area_invalida_e_bloqueada(self):
        for campo, valor in [('AREA_KM2', 0), ('DENSIDADE_DRENAGEM_KM2', np.nan), ('COBERTURA_COLETA', 2)]:
            a = areas_teste();a.loc[0, campo] = valor
            with self.subTest(campo=campo), self.assertRaises(ValueError):
                conf.classificar_areas(a)


if __name__ == '__main__':
    unittest.main()
