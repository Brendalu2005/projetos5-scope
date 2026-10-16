"""Verifica classificação e bloqueio de exportações com situações pendentes."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

NOTEBOOK = Path(__file__).resolve().parents[1] / 'camada_preventiva/notebooks/camada_preventiva.ipynb'
CELULAS = json.loads(NOTEBOOK.read_text(encoding='utf-8'))['cells']
AUXILIARES = ''.join(CELULAS[3]['source']).replace(
    'from IPython.display import display', 'display = lambda *args: None')
CLASSIFICAR = ''.join(CELULAS[9]['source'])
SALVAR = ''.join(CELULAS[23]['source'])


class SituacoesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pasta = Path(self.temp.name)
        self.ns = {'os': os, 'SAIDAS': str(self.pasta), 'PROC': str(self.pasta)}
        exec(AUXILIARES, self.ns)
        self.csv = self.pasta / 'situacoes_unicas.csv'

    def executar(self, source):
        with contextlib.redirect_stdout(io.StringIO()):
            exec(source, self.ns)

    def conferir_bloqueio(self):
        destino = self.pasta / '156_limpo.parquet'
        destino.write_bytes(b'arquivo anterior preservado')
        with self.assertRaisesRegex(ValueError, 'Não é possível salvar'):
            self.executar(SALVAR)
        self.assertEqual(destino.read_bytes(), b'arquivo anterior preservado')

    def test_variacoes_sao_classificadas_sem_alterar_ordem_ou_origem(self):
        valores = [' atendida ', 'CADASTRADA', 'PREPARAO', 'PREPARACAO',
                   'EXECUO', 'EXECUCAO', 'FISCALIZAO', 'FISCALIZACAO', 'PENDENCIA', 'PENDENTE']
        self.ns['df'] = pd.DataFrame({'SITUACAO': valores, 'ID': range(len(valores))})
        self.executar(CLASSIFICAR)
        df = self.ns['df']
        self.assertEqual(df['SITUACAO_ORIGINAL'].tolist(), valores)
        self.assertEqual(df['ID'].tolist(), list(range(len(valores))))
        self.assertEqual(df['SITUACAO_TIPO'].tolist(), ['FINAL'] + ['ABERTA'] * 9)
        self.assertEqual(df['SITUACAO'].nunique(), 7)
        anterior = df.copy()
        self.executar(CLASSIFICAR)
        pd.testing.assert_frame_equal(self.ns['df'], anterior)

    def test_decisao_manual_valida_prevalece_sobre_padrao(self):
        self.csv.write_text('situacao,n,tipo\n cadastrada ,1, final \n', encoding='utf-8')
        self.ns['df'] = pd.DataFrame({'SITUACAO': ['CADASTRADA', 'ATENDIDA']})
        self.executar(CLASSIFICAR)
        self.assertEqual(self.ns['df']['SITUACAO_TIPO'].tolist(), ['FINAL', 'FINAL'])

    def test_grafias_duplicadas_no_csv_nao_multiplicam_registros(self):
        self.csv.write_text('situacao,n,tipo\nPREPARAO,1,\nPREPARACAO,1,ABERTA\n', encoding='utf-8')
        self.ns['df'] = pd.DataFrame({'SITUACAO': ['PREPARAO', 'PREPARACAO']})
        self.executar(CLASSIFICAR)
        tabela = pd.read_csv(self.csv)
        self.assertEqual(len(tabela), 1)
        self.assertEqual(int(tabela['n'].sum()), 2)
        self.assertEqual(len(self.ns['df']), 2)

    def test_situacoes_novas_ou_ausentes_param_e_bloqueiam_exportacao(self):
        for situacao in ['NOVA SITUACAO', None, '   ']:
            with self.subTest(situacao=situacao):
                self.ns['df'] = pd.DataFrame({'SITUACAO': ['ATENDIDA', situacao]})
                with self.assertRaisesRegex(ValueError, 'PAUSA MANUAL'):
                    self.executar(CLASSIFICAR)
                self.assertEqual(int(self.ns['df']['SITUACAO_TIPO'].isna().sum()), 1)
                self.conferir_bloqueio()

    def test_tipo_invalido_no_csv_nao_e_aceito_como_classificado(self):
        self.csv.write_text('situacao,n,tipo\nATENDIDA,1,FECHADA\n', encoding='utf-8')
        self.ns['df'] = pd.DataFrame({'SITUACAO': ['ATENDIDA']})
        with self.assertRaisesRegex(ValueError, 'PAUSA MANUAL'):
            self.executar(CLASSIFICAR)
        self.conferir_bloqueio()

    def test_conflito_entre_grafias_invalida_classificacao_anterior(self):
        self.csv.write_text('situacao,n,tipo\nPREPARAO,1,ABERTA\nPREPARACAO,1,FINAL\n', encoding='utf-8')
        self.ns['df'] = pd.DataFrame({'SITUACAO': ['PREPARAO'], 'SITUACAO_TIPO': ['FINAL']})
        with self.assertRaisesRegex(ValueError, 'conflitantes'):
            self.executar(CLASSIFICAR)
        self.conferir_bloqueio()

    def test_exportacao_e_bloqueada_se_classificacao_for_pulada(self):
        self.ns['df'] = pd.DataFrame({'SITUACAO': ['ATENDIDA']})
        self.conferir_bloqueio()


if __name__ == '__main__':
    unittest.main()
