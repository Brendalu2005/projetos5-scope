"""Baixa e valida os nove arquivos brutos usados pelo notebook (sprint 1)."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PORTAL = "https://dados.recife.pe.gov.br"
DESTINO_PADRAO = Path(__file__).resolve().parents[1] / "dados" / "bruto"
MANIFESTO = "manifesto_downloads.json"
DATASET_156 = "central-de-atendimento-de-servicos-da-emlurb-156"
COLUNAS_156 = frozenset({
    "GRUPOSERVICO_DESCRICAO", "DATA_DEMANDA", "DATA_ULT_SITUACAO",
    "SITUACAO", "LATITUDE", "LONGITUDE", "BAIRRO", "RPA",
})


@dataclass(frozen=True)
class Base:
    arquivo: str
    dataset: str
    recurso: str
    colunas: frozenset[str] = frozenset()


# IDs fixos: o portal possui publicações de 2024/2025 com nomes iguais.
# Estes recursos correspondem aos arquivos da execução histórica do projeto.
BASES = (
    Base("156_2023.csv", DATASET_156, "30ba6cc0-17a8-4f38-8c86-55184c87ead1", COLUNAS_156),
    Base("156_2024.csv", DATASET_156, "031c3ad3-265f-4d9c-830a-7d1f85f830fa", COLUNAS_156),
    Base("156_2025.csv", DATASET_156, "06872af2-9350-4c34-aa39-6337a1822ff7", COLUNAS_156),
    Base("bairros.geojson", "area-urbana", "5c67ce14-1799-40c4-a37c-9daa04d1761c"),
    Base("drenagem_elementos.geojson", "nucleos-urbanizados", "de0680a3-1700-465f-b9fd-2876664495f1"),
    Base("drenagem_rede.geojson", "nucleos-urbanizados", "161b5234-b56e-48c4-8d82-eab667423f69"),
    Base("setores_coleta.geojson", "nucleos-urbanizados", "c4ee68b6-8fe5-4ac4-9776-206d152e5a4e"),
    Base("roteiros_coleta.csv", "roteiro-de-coleta", "2a0dd9cf-86f4-4ceb-83c1-7e39ff239cb0",
         frozenset({"SETOR", "ENDERECO", "TURNO", "FREQUENCIA"})),
    # Opcional no roteiro, mas lido incondicionalmente pelo notebook atual.
    Base("onde_descartar.csv", "destinacao-de-residuos-solidos", "24adb60a-9fb2-4f55-b1d5-808729f5f28e",
         frozenset({"TIPORESIDUO", "LATITUDE", "LONGITUDE"})),
)


def agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalizar(texto: str) -> str:
    texto = "".join(c for c in unicodedata.normalize("NFKD", texto)
                    if not unicodedata.combining(c))
    return re.sub(r"\W+", "_", texto.upper().strip()).strip("_")


def sha256(caminho: Path) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def validar(caminho: Path, base: Base) -> dict:
    """Rejeita arquivos vazios, páginas de erro e formatos incompatíveis."""
    if caminho.stat().st_size == 0:
        raise ValueError("Arquivo vazio.")
    if base.arquivo.endswith(".geojson"):
        with caminho.open(encoding="utf-8-sig") as arquivo:
            dados = json.load(arquivo)
        if not isinstance(dados, dict) or dados.get("type") != "FeatureCollection":
            raise ValueError("Era esperado um GeoJSON do tipo FeatureCollection.")
        feicoes = dados.get("features")
        if not isinstance(feicoes, list) or not feicoes:
            raise ValueError("GeoJSON sem feições.")
        if any(not isinstance(f, dict) or f.get("type") != "Feature" for f in feicoes):
            raise ValueError("GeoJSON contém feições inválidas.")
        return {"formato": "GeoJSON", "registros": len(feicoes)}
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            with caminho.open(encoding=encoding, newline="") as arquivo:
                amostra = arquivo.read(8192)
                arquivo.seek(0)
                dialect = csv.Sniffer().sniff(amostra, delimiters=";,")
                leitor = csv.reader(arquivo, dialect, strict=True)
                colunas = [normalizar(c) for c in next(leitor)]
                faltantes = base.colunas - set(colunas)
                if faltantes:
                    raise ValueError(f"CSV sem colunas obrigatórias: {sorted(faltantes)}")
                total = 0
                for linha in leitor:
                    if not linha or not any(c.strip() for c in linha):
                        continue
                    if len(linha) != len(colunas):
                        raise ValueError(f"CSV com número de campos inválido na linha {leitor.line_num}.")
                    total += 1
                if not total:
                    raise ValueError("CSV sem registros.")
                return {"formato": "CSV", "registros": total, "encoding": encoding,
                        "separador": dialect.delimiter, "colunas": colunas}
        except UnicodeDecodeError:
            continue
    raise ValueError("Não foi possível decodificar o CSV.")


def sessao_http() -> requests.Session:
    sessao = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"])
    sessao.mount("https://", HTTPAdapter(max_retries=retry))
    sessao.headers.update({"User-Agent": "ConectaRecife-CamadaPreventiva/1.0",
                           "Accept-Encoding": "identity"})
    return sessao


def baixar(base: Base, pasta: Path, sessao: requests.Session, timeout: int,
           sobrescrever: bool, anterior: dict) -> dict:
    destino = pasta / base.arquivo
    if destino.exists() and not sobrescrever:
        validacao = validar(destino, base)
        digest = sha256(destino)
        conhecido = anterior.get("sha256") == digest
        if anterior.get("sha256") and not conhecido:
            raise ValueError("Arquivo local difere do SHA-256 registrado. Confira-o ou use --sobrescrever.")
        registro = dict(anterior) if conhecido else {
            "arquivo": base.arquivo, "dataset": f"{PORTAL}/dataset/{base.dataset}",
            "recurso_id": base.recurso, "origem": "arquivo_local_nao_baixado_pelo_script",
        }
        registro.update(validacao, sha256=digest, bytes=destino.stat().st_size,
                        acao="reutilizado", verificado_em_utc=agora())
        return registro
    with sessao.get(f"{PORTAL}/api/3/action/resource_show", params={"id": base.recurso},
                    timeout=(10, timeout)) as resposta:
        resposta.raise_for_status()
        payload = resposta.json()
    if not payload.get("success"):
        raise ValueError(f"O portal não retornou o recurso {base.recurso}.")
    recurso = payload["result"]
    url = recurso["url"]
    if not url.startswith("https://"):
        raise ValueError(f"O recurso não aponta para um download HTTPS: {url}")
    temporario = None
    try:
        with tempfile.NamedTemporaryFile(dir=pasta, prefix=f".{base.arquivo}.",
                                         suffix=".part", delete=False) as arquivo:
            temporario = Path(arquivo.name)
            with sessao.get(url, stream=True, timeout=(10, timeout)) as resposta:
                resposta.raise_for_status()
                total = 0
                for bloco in resposta.iter_content(chunk_size=1024 * 1024):
                    if bloco:
                        arquivo.write(bloco)
                        total += len(bloco)
                esperado = resposta.headers.get("Content-Length")
                if esperado and not resposta.headers.get("Content-Encoding") and total != int(esperado):
                    raise ValueError(f"Download incompleto: {total} de {esperado} bytes.")
        validacao = validar(temporario, base)
        digest = sha256(temporario)
        registro = {
            "arquivo": base.arquivo, "dataset": f"{PORTAL}/dataset/{base.dataset}",
            "recurso_id": base.recurso, "recurso_nome": recurso.get("name"),
            "url_download": url, "atualizado_no_portal": recurso.get("last_modified"),
            "baixado_em_utc": agora(), "origem": "portal_oficial", "acao": "baixado",
            "bytes": total, "sha256": digest, **validacao,
        }
        # Só publica o arquivo após download e validação completos.
        os.replace(temporario, destino)
        return registro
    finally:
        if temporario is not None:
            temporario.unlink(missing_ok=True)


def salvar_manifesto(caminho: Path, registros: dict, erros: dict) -> None:
    dados = {"verificado_em_utc": agora(), "arquivos": registros, "erros": erros}
    temporario = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=caminho.parent,
                                         suffix=".part", delete=False) as arquivo:
            temporario = Path(arquivo.name)
            json.dump(dados, arquivo, ensure_ascii=False, indent=2)
            arquivo.write("\n")
        os.replace(temporario, caminho)
    finally:
        if temporario is not None:
            temporario.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destino", type=Path, default=DESTINO_PADRAO,
                        help="Pasta de dados brutos; padrão: camada_preventiva/dados/bruto.")
    parser.add_argument("--sobrescrever", action="store_true",
                        help="Baixa novamente, substituindo arquivos somente após validação.")
    parser.add_argument("--timeout", type=int, default=120,
                        help="Limite de espera por resposta, em segundos (padrão: 120).")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout deve ser maior que zero.")
    pasta = args.destino.expanduser().resolve()
    pasta.mkdir(parents=True, exist_ok=True)
    manifesto = pasta / MANIFESTO
    registros = {}
    if manifesto.exists():
        with manifesto.open(encoding="utf-8") as arquivo:
            registros = json.load(arquivo)["arquivos"]
    erros = {}
    print(f"Destino: {pasta}", flush=True)
    with sessao_http() as sessao:
        for i, base in enumerate(BASES, 1):
            print(f"[{i}/{len(BASES)}] {base.arquivo}...", flush=True)
            try:
                registro = baixar(base, pasta, sessao, args.timeout, args.sobrescrever,
                                  registros.get(base.arquivo, {}))
                registros[base.arquivo] = registro
                print(f"  OK: {registro['acao']}; {registro['registros']:,} registros; "
                      f"{registro['bytes'] / 1e6:.1f} MB", flush=True)
            except (requests.RequestException, ValueError, KeyError, OSError, csv.Error) as erro:
                erros[base.arquivo] = str(erro)
                print(f"  ERRO: {erro}", file=sys.stderr, flush=True)
            salvar_manifesto(manifesto, registros, erros)
    print(f"\nConcluído: {len(BASES) - len(erros)}/{len(BASES)} arquivos disponíveis.")
    print(f"Fontes e integridade: {manifesto}")
    if erros:
        print("Corrija as falhas e execute novamente; os arquivos válidos serão reutilizados.",
              file=sys.stderr)
        return 1
    print("Dados brutos preparados. A base processada será gerada ao executar o notebook.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
