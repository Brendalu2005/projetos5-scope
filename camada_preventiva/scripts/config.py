"""Configuração central do projeto. Tudo que o grupo pode precisar calibrar fica aqui."""
from pathlib import Path

# Raiz = pasta camada_preventiva (um nível acima de scripts/)
RAIZ = Path(__file__).resolve().parent.parent
BRUTO = RAIZ / "dados" / "brutos"
PROCESSADO = RAIZ / "dados" / "processados"
SAIDAS = RAIZ / "saidas"

ANOS = [2023, 2024, 2025]

# Contorno dos bairros (usado no mapa de densidade)
BAIRROS_GEOJSON = BRUTO / "bairros.geojson"

# ---- Sprint 1: URLs de download -------------------------------------------
# Cole aqui os links diretos ("baixar CSV" / "arquivo") do Portal de Dados
# Abertos do Recife. Deixe "" para pular (e coloque o arquivo manualmente
# em dados/brutos/ com o mesmo nome).
URLS = {
    "156_2023.csv": "",
    "156_2024.csv": "",
    "156_2025.csv": "",
    "drenagem_elementos.geojson": "",
    "drenagem_rede.geojson": "",
    "setores_coleta.geojson": "",
    "roteiro_coleta.csv": "",
    "bairros.geojson": "",
}

# ---- Sprint 2: recorte -----------------------------------------------------
# Termos (sem acento, maiúsculo) procurados em GRUPOSERVICO_DESCRICAO.
GRUPOS_RECORTE = ["LIMPEZA URBANA", "DRENAGEM"]
# O roteiro sugere 1 categoria + 1 RPA piloto. Ex.: RPA_PILOTO = 3.
# Use None para analisar a cidade toda.
RPA_PILOTO = None

# Caixa aproximada do Recife (lat/lon). Depois refinamos com o limite dos bairros.
LAT_MIN, LAT_MAX = -8.20, -7.90
LON_MIN, LON_MAX = -35.10, -34.82
