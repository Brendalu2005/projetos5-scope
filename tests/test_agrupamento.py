"""Verifica raio fixo, histórico e rastreabilidade do agrupamento territorial."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

NOTEBOOK = Path(__file__).resolve().parents[1] / 'camada_preventiva/notebooks/camada_preventiva.ipynb'
CELULAS = json.loads(NOTEBOOK.read_text(encoding='utf-8'))['cells']
AUXILIARES = ''.join(CELULAS[3]['source']).replace(
    'from IPython.display import display', 'display = lambda *args: None')


class AgrupamentoTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pasta = Path(self.temp.name)
        self.ns = {'os': os, 'PROC': str(self.pasta), 'SAIDAS': str(self.pasta),
                   'RAIO_M': 80, 'CRS_M': 'EPSG:31985',
                   'GRUPOS_RECORTE': ['DRENAGEM'], 'RPA_PILOTO': 4}
        exec(AUXILIARES, self.ns)

    def base(self, xs):
        n = len(xs)
        datas = pd.date_range('2023-01-01', periods=n)
        return pd.DataFrame({
            'ID_LINHA': range(n), 'X': np.array(xs, dtype=float) + 290000,
            'Y': [9100000.] * n, 'BAIRRO': ['BAIRRO TESTE'] * n,
            'RPA_NUM': [4] * n, 'GRUPOSERVICO_DESCRICAO': ['DRENAGEM'] * n,
            'DATA_DEMANDA': datas, 'DATA_ULT_SITUACAO': datas,
            'SITUACAO': ['CADASTRADA'] * n, 'SITUACAO_TIPO': ['ABERTA'] * n,
            'COORD_VALIDA': [True] * n})

    def celula(self, i):
        with contextlib.redirect_stdout(io.StringIO()):
            exec(''.join(CELULAS[i]['source']), self.ns)

    def executar(self, base):
        base.to_parquet(self.pasta / '156_limpo.parquet', index=False)
        for i in [25, 26, 27, 28]:
            self.celula(i)
        return self.ns['denuncias_pontos'], self.ns['pontos']

    def test_raio_inclui_limite_e_preserva_isolados(self):
        historico, pontos = self.executar(self.base([0, 80, 80.001, 160, 1000]))
        ids = historico.set_index('ID_LINHA')['ID_PONTO']
        self.assertEqual(ids[0], ids[1])
        self.assertNotEqual(ids[0], ids[2])
        self.assertEqual(ids[2], ids[3])
        self.assertEqual(sorted(pontos['N_DENUNCIAS']), [1, 2, 2])
        self.assertTrue(historico['DISTANCIA_M'].le(80).all())
        self.assertEqual(set(historico['ID_LINHA']), set(range(5)))

    def test_grupo_nao_cresce_por_encadeamento(self):
        historico, pontos = self.executar(self.base([0, 70, 140]))
        ids = historico.set_index('ID_LINHA')['ID_PONTO']
        self.assertEqual(ids[0], ids[1])
        self.assertNotEqual(ids[0], ids[2])
        self.assertEqual(len(pontos), 2)

    def test_ordem_das_linhas_nao_muda_os_grupos(self):
        base = self.base([0, 70, 140, 210, 1000])
        a, _ = self.executar(base)
        esperado = a[['ID_LINHA', 'ID_PONTO']].sort_values('ID_LINHA').reset_index(drop=True)
        b, _ = self.executar(base.sample(frac=1, random_state=7))
        obtido = b[['ID_LINHA', 'ID_PONTO']].sort_values('ID_LINHA').reset_index(drop=True)
        pd.testing.assert_frame_equal(esperado, obtido)

    def test_historico_considera_atualizacao_e_conta_as_situacoes(self):
        base = self.base([0, 10, 20])
        base['DATA_ULT_SITUACAO'] = pd.to_datetime(['2023-01-07', '2023-01-04', '2023-01-05'])
        base['SITUACAO'] = ['CADASTRADA', 'ATENDIDA', 'ATENDIDA']
        base['SITUACAO_TIPO'] = ['ABERTA', 'FINAL', 'FINAL']
        _, pontos = self.executar(base)
        ponto = pontos.iloc[0]
        self.assertEqual(ponto['N_DENUNCIAS'], 3)
        self.assertEqual(ponto['N_ABERTAS'], 1)
        self.assertEqual(ponto['N_FINAIS'], 2)
        self.assertEqual(ponto['PRIMEIRA_DATA'], pd.Timestamp('2023-01-01'))
        self.assertEqual(ponto['ULTIMA_DATA'], pd.Timestamp('2023-01-03'))
        self.assertEqual(ponto['ULTIMA_ATUALIZACAO'], pd.Timestamp('2023-01-07'))
        self.assertEqual(ponto['ULTIMA_SITUACAO'], 'CADASTRADA')
        self.assertEqual(ponto['SITUACAO_PONTO'], 'ABERTA')

    def test_sem_coordenada_fica_na_base_e_nao_recebe_ponto(self):
        base = self.base([0, 20, 200])
        base.loc[2, 'COORD_VALIDA'] = False
        base.loc[2, ['X', 'Y']] = np.nan
        historico, pontos = self.executar(base)
        self.assertEqual(set(historico['ID_LINHA']), {0, 1})
        self.assertEqual(int(pontos['N_DENUNCIAS'].sum()), 2)
        pd.testing.assert_frame_equal(pd.read_parquet(self.pasta / '156_limpo.parquet'), base)
        salvo = gpd.read_parquet(self.pasta / 'pontos_agrupados.parquet')
        self.assertEqual(salvo.crs.to_epsg(), 31985)
        self.assertEqual(len(pd.read_csv(self.pasta / 'pontos_agrupados.csv')), 1)

    def test_entrada_invalida_e_bloqueada(self):
        for caso in ['sem_coordenadas', 'xy_invalido', 'id_duplicado', 'situacao_pendente',
                     'outra_rpa', 'rpa_ausente', 'outra_categoria', 'data_ausente']:
            with self.subTest(caso=caso):
                base = self.base([0, 10])
                if caso == 'sem_coordenadas': base['COORD_VALIDA'] = False
                if caso == 'xy_invalido': base.loc[0, 'X'] = np.nan
                if caso == 'id_duplicado': base['ID_LINHA'] = 0
                if caso == 'situacao_pendente': base.loc[0, 'SITUACAO_TIPO'] = None
                if caso == 'outra_rpa': base.loc[0, 'RPA_NUM'] = 3
                if caso == 'rpa_ausente': base['RPA_NUM'] = pd.Series([4, pd.NA], dtype='Int64')
                if caso == 'outra_categoria': base.loc[0, 'GRUPOSERVICO_DESCRICAO'] = 'MACRODRENAGEM'
                if caso == 'data_ausente': base.loc[0, 'DATA_DEMANDA'] = pd.NaT
                with self.assertRaises(ValueError): self.executar(base)

    def test_raio_e_unidade_incorretos_sao_bloqueados(self):
        for raio in [0, -80, np.nan, np.inf]:
            with self.subTest(raio=raio):
                self.ns['RAIO_M'] = raio
                with self.assertRaises(ValueError): self.executar(self.base([0]))
        self.ns['RAIO_M'] = 80
        for crs in ['EPSG:4326', 'EPSG:2263']:
            with self.subTest(crs=crs):
                self.ns['CRS_M'] = crs
                with self.assertRaises(ValueError): self.executar(self.base([0]))

    def test_exportacao_bloqueia_denuncia_fora_do_raio(self):
        self.executar(self.base([0, 20]))
        caminho = self.pasta / 'pontos_agrupados.parquet'
        anterior = caminho.read_bytes()
        self.ns['denuncias_pontos'].loc[0, 'DISTANCIA_M'] = 81
        with self.assertRaisesRegex(ValueError, 'além do raio'): self.celula(28)
        self.assertEqual(caminho.read_bytes(), anterior)


if __name__ == '__main__':
    unittest.main()
