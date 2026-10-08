"""Sprint 1 - baixa os dados brutos para dados/brutos/ (pula o que já existe)."""
import requests
from config import BRUTO, URLS


def baixar(nome, url):
    destino = BRUTO / nome
    if destino.exists():
        print(f"ok (já existe): {nome}")
        return
    if not url:
        print(f"PULADO (sem URL em config.py): {nome}")
        return
    print(f"baixando {nome} ...")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(destino, "wb") as f:
            for bloco in r.iter_content(1 << 20):
                f.write(bloco)
    print(f"  {destino.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    BRUTO.mkdir(parents=True, exist_ok=True)
    for nome, url in URLS.items():
        baixar(nome, url)
    print("\nConteúdo de dados/brutos/:")
    for p in sorted(BRUTO.iterdir()):
        print(f"  {p.name}  {p.stat().st_size / 1e6:.1f} MB")
