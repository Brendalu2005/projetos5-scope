"""Verifica integridade e preservação de arquivos quando o download falha."""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

SCRIPT = Path(__file__).resolve().parents[1] / "camada_preventiva/scripts/01_baixar_dados.py"
spec = importlib.util.spec_from_file_location("baixar_dados", SCRIPT)
download = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = download
spec.loader.exec_module(download)


class Resposta:
    def __init__(self, corpo=b"", falha=None, metadata=None):
        self.corpo = corpo
        self.falha = falha
        self.metadata = metadata
        self.headers = {"Content-Length": str(len(corpo))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        pass

    def json(self):
        return self.metadata

    def iter_content(self, chunk_size):
        yield self.corpo
        if self.falha:
            raise self.falha


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pasta = Path(self.temp.name)
        self.base = download.Base("teste.csv", "teste", "id", frozenset({"NOME", "VALOR"}))
        self.arquivo = self.pasta / self.base.arquivo
        self.original = b"nome;valor\noriginal;1\n"
        self.arquivo.write_bytes(self.original)

    def sessao(self, resposta):
        metadata = {"success": True, "result": {"url": "https://dados.recife.pe.gov.br/teste.csv"}}
        return Mock(get=Mock(side_effect=[Resposta(metadata=metadata), resposta]))

    def test_resposta_html_nao_substitui_arquivo_anterior(self):
        sessao = self.sessao(Resposta(b"<html>Falha no servidor</html>"))
        with self.assertRaises((ValueError, download.csv.Error)):
            download.baixar(self.base, self.pasta, sessao, 10, True, {})
        self.assertEqual(self.arquivo.read_bytes(), self.original)
        self.assertFalse(list(self.pasta.glob("*.part")))

    def test_interrupcao_nao_publica_download_parcial(self):
        resposta = Resposta(b"nome;valor\nparcial;2\n", falha=requests.ConnectionError("queda"))
        with self.assertRaises(requests.ConnectionError):
            download.baixar(self.base, self.pasta, self.sessao(resposta), 10, True, {})
        self.assertEqual(self.arquivo.read_bytes(), self.original)
        self.assertFalse(list(self.pasta.glob("*.part")))

    def test_reutilizacao_confere_hash_e_preserva_origem_sem_acessar_rede(self):
        sessao = Mock()
        anterior = {"sha256": download.sha256(self.arquivo), "baixado_em_utc": "data-original",
                    "origem": "portal_oficial"}
        registro = download.baixar(self.base, self.pasta, sessao, 10, False, anterior)
        sessao.get.assert_not_called()
        self.assertEqual(registro["acao"], "reutilizado")
        self.assertEqual(registro["baixado_em_utc"], "data-original")
        self.assertEqual(registro["origem"], "portal_oficial")
        self.assertEqual(registro["registros"], 1)

    def test_alteracao_local_e_detectada_sem_sobrescrever(self):
        sessao = Mock()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            download.baixar(self.base, self.pasta, sessao, 10, False, {"sha256": "hash-diferente"})
        sessao.get.assert_not_called()
        self.assertEqual(self.arquivo.read_bytes(), self.original)

    def test_falha_individual_e_registrada_e_nao_impede_arquivo_seguinte(self):
        outra = download.Base("outro.csv", "teste", "outro")
        registro = {"acao": "baixado", "registros": 1, "bytes": 10}
        with patch.object(download, "BASES", (self.base, outra)), \
             patch.object(download, "baixar", side_effect=[ValueError("falha simulada"), registro]) as baixar, \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            resultado = download.main(["--destino", str(self.pasta)])
        self.assertEqual(resultado, 1)
        self.assertEqual(baixar.call_count, 2)
        manifesto = json.loads((self.pasta / download.MANIFESTO).read_text())
        self.assertIn(self.base.arquivo, manifesto["erros"])
        self.assertIn(outra.arquivo, manifesto["arquivos"])


if __name__ == "__main__":
    unittest.main()
