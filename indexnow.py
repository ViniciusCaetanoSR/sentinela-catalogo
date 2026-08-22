"""Ping do IndexNow com as URLs que realmente mudaram.

O gerador escrevia o arquivo de chave e parava ali. Chave sozinha so prova
posse do dominio - sem o POST, nada e submetido, e a funcao inteira era
decorativa. Este script fecha a metade que faltava.

Roda depois do deploy, no workflow. Sem dependencias.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

RAIZ = os.path.dirname(os.path.abspath(__file__))
ARQ_CONFIG = os.path.join(RAIZ, "config.json")
# No CI o arquivo chega pelo artefato, na raiz; localmente ele esta em site/.
CANDIDATOS = (os.path.join(RAIZ, "mudancas.txt"),
              os.path.join(RAIZ, "site", "mudancas.txt"))
ENDPOINT = "https://api.indexnow.org/IndexNow"
# O proprio protocolo limita o lote. Acima disso o gerador ja teria podado.
MAXIMO = 200


def config():
    if not os.path.exists(ARQ_CONFIG):
        return {}
    with open(ARQ_CONFIG, encoding="utf-8") as f:
        return json.load(f)


def urls():
    for caminho in CANDIDATOS:
        if os.path.exists(caminho):
            with open(caminho, encoding="utf-8") as f:
                return [l.strip() for l in f if l.strip()]
    return []


def main():
    cfg = config()
    chave = cfg.get("indexnow_key")
    if not chave:
        print("indexnow_key vazio em config.json - nada a submeter.")
        return 0

    lista = urls()
    if not lista:
        print("Nenhuma URL mudou; nada a submeter.")
        return 0
    if len(lista) > MAXIMO:
        print(f"{len(lista)} URLs e lote demais - submetendo so a raiz.")
        lista = lista[:1]

    anfitriao = urllib.parse.urlsplit(lista[0]).netloc
    corpo = json.dumps({
        "host": anfitriao,
        "key": chave,
        "keyLocation": f"https://{anfitriao}/{chave}.txt",
        "urlList": lista,
    }).encode("utf-8")

    req = urllib.request.Request(
        ENDPOINT, data=corpo, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"IndexNow respondeu {r.status} para {len(lista)} URLs.")
    except urllib.error.HTTPError as e:
        # 422 costuma ser chave nao encontrada no dominio. Nao e motivo para
        # derrubar o workflow: o site ja foi publicado.
        print(f"IndexNow devolveu {e.code}: {e.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"IndexNow inacessivel: {e.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
