import re
import unicodedata
import pandas as pd


def sem_acento(s):
    if pd.isna(s):
        return s
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c))


def norm_texto(s):
    """Maiúsculo, sem acento, espaços únicos."""
    if pd.isna(s):
        return s
    return re.sub(r"\s+", " ", sem_acento(s).upper().strip())


def norm_coluna(c):
    return re.sub(r"\W+", "_", norm_texto(c)).strip("_")


class TabelaPerdas:
    """Registra linhas que entraram/saíram em cada etapa (entrega 2)."""

    def __init__(self):
        self.linhas = []

    def log(self, etapa, entrou, saiu, obs=""):
        self.linhas.append({"etapa": etapa, "entrou": entrou, "saiu": saiu,
                            "perdeu": entrou - saiu, "obs": obs})
        print(f"[{etapa}] {entrou:,} -> {saiu:,} (perdeu {entrou - saiu:,}) {obs}")

    def df(self):
        return pd.DataFrame(self.linhas)


ABREVIACOES = {
    r"\bAV\b": "AVENIDA", r"\bR\b": "RUA", r"\bTV\b": "TRAVESSA",
    r"\bPC\b": "PRACA", r"\bEST\b": "ESTRADA", r"\bAL\b": "ALAMEDA",
    r"\bLG\b": "LARGO", r"\bDR\b": "DOUTOR", r"\bPROF\b": "PROFESSOR",
    r"\bCEL\b": "CORONEL", r"\bSTA\b": "SANTA", r"\bSTO\b": "SANTO",
    r"\b1TR\b": "PRIMEIRA TRAVESSA", r"\b2TR\b": "SEGUNDA TRAVESSA",
    r"\b3TR\b": "TERCEIRA TRAVESSA",
}


def expandir_abrev(s):
    if pd.isna(s):
        return s
    for pat, rep in ABREVIACOES.items():
        s = re.sub(pat, rep, s)
    return s
