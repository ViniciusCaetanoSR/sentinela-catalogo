"""Ping do IndexNow com as URLs que realmente mudaram.

O gerador escrevia o arquivo de chave e parava ali. Chave sozinha só prova
posse do domínio — sem o POST, nada é submetido, e a função inteira era
decorativa. Este script fecha a metade que faltava.

Roda depois do deploy, no workflow. Sem dependências. Toda falha aqui é
tolerada pelo workflow (`continue-on-error`): o site já foi publicado, e o
ping é cortesia para o indexador, não condição de sucesso do dia.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import comum

ARQ_CONFIG = comum.padrao().config
# No CI o arquivo chega pelo artefato, na raiz; localmente ele está em site/.
CANDIDATOS = (
    os.path.join(comum.RAIZ, "mudancas.txt"),
    os.path.join(comum.padrao().site, "mudancas.txt"),
)
ENDPOINT = "https://api.indexnow.org/IndexNow"
# Espelha TETO_INDEXNOW do gerador, não o protocolo: o IndexNow aceita até
# 10 000 URLs por POST. Acima de 200 o build não "mudou conteúdo", foi refeito
# do zero (primeira geração, troca de template, lastmod.json perdido), e o
# gerador já terá podado a lista para a raiz. Se chegar mais do que isso aqui,
# alguém gerou o mudancas.txt por fora — a poda se repete por garantia.
MAXIMO = 200
# O mesmo User-Agent do coletor, sem importar o coletor: o import arrastaria
# zoneinfo e a lógica de coleta para um script que só faz um POST, e um erro
# lá derrubaria o ping por motivo alheio a ele. comum.py não tem nada disso.
AGENTE = comum.AGENTE
# Quantos bytes do corpo de erro vão para o log. O IndexNow responde 422 com
# um JSON curto dizendo o motivo (chave não encontrada, host divergente);
# sem ele o código sozinho não diz o que corrigir.
CORPO_NO_LOG = 300


def ler_urls(candidatos):
    """Lista de URLs do primeiro candidato que existir, uma por linha.

    Linhas em branco são ignoradas para que um `echo >>` acidental no
    workflow não vire uma URL vazia no payload (o IndexNow rejeita o lote
    inteiro por uma entrada inválida).
    """
    for caminho in candidatos:
        if os.path.exists(caminho):
            with open(caminho, encoding="utf-8") as f:
                return [linha.strip() for linha in f if linha.strip()]
    return []


def _host(url):
    return urllib.parse.urlsplit(url).netloc


def problema_de_configuracao(cfg, lista):
    """Mensagem do erro de configuração que impede o ping, ou None.

    Tudo aqui é erro de quem configurou, não da rede: nenhum destes casos
    melhora tentando de novo, e todos dariam 422 no endpoint (keyLocation
    fora do host, host vazio). Separar isso do POST evita gastar uma
    requisição para descobrir o que já dava para ver em config.json.
    """
    base = cfg.get("base_url", "")
    if not base:
        return (
            "base_url vazio em config.json - sem ele o keyLocation não tem "
            "onde ficar; nada a submeter."
        )
    partes = urllib.parse.urlsplit(lista[0])
    if partes.scheme != "https" or not partes.netloc:
        return (
            f"primeira URL de mudancas.txt não é absoluta https: {lista[0]!r} "
            "- o gerador precisa de base_url para escrevê-las; nada a submeter."
        )
    if _host(base) != partes.netloc:
        return (
            f"base_url aponta para {_host(base)!r} mas as URLs são de "
            f"{partes.netloc!r} - o keyLocation ficaria fora do host; nada a "
            "submeter."
        )
    return None


def filtrar_host(lista):
    """Separa as URLs no host da primeira das que estão em outro host.

    O protocolo exige um host por POST; uma URL estranha no meio invalida o
    lote inteiro. Em vez de abortar, mantém o que dá para mandar e devolve o
    resto para o chamador avisar.
    """
    host = _host(lista[0])
    mantidas = [u for u in lista if _host(u) == host]
    descartadas = [u for u in lista if _host(u) != host]
    return mantidas, descartadas


def montar_payload(cfg, chave, lista):
    """Corpo do POST. `host` é o netloc das URLs; `keyLocation` segue o
    base_path, porque em Pages de projeto a raiz do host não é nossa e o
    arquivo de chave está servido sob /<repo>/."""
    return {
        "host": _host(lista[0]),
        "key": chave,
        "keyLocation": comum.absoluta(cfg, "/" + chave + ".txt"),
        "urlList": list(lista),
    }


def _corpo_do_erro(e):
    """Primeiros bytes da resposta de erro, legíveis, ou vazio se não há corpo."""
    # KeyError: no 3.9, um HTTPError sem corpo (fp=None) delega .read() a um
    # invólucro de tempfile que levanta KeyError('file') em vez de
    # AttributeError. Entre 3.9 e 3.12 é a única diferença.
    try:
        bruto = e.read()
    except (AttributeError, KeyError, OSError):
        return ""
    return bruto[:CORPO_NO_LOG].decode("utf-8", errors="replace").strip()


def main():
    cfg = comum.carregar_config(ARQ_CONFIG)
    chave = cfg.get("indexnow_key")
    if not chave:
        print("indexnow_key vazio em config.json - nada a submeter.")
        return 0

    lista = ler_urls(CANDIDATOS)
    if not lista:
        print("Nenhuma URL mudou; nada a submeter.")
        return 0

    problema = problema_de_configuracao(cfg, lista)
    if problema:
        print(problema)
        return 0

    lista, descartadas = filtrar_host(lista)
    if descartadas:
        print(
            f"{len(descartadas)} URL(s) fora do host {_host(lista[0])} "
            f"descartada(s); a primeira: {descartadas[0]}",
            file=sys.stderr,
        )
    if len(lista) > MAXIMO:
        print(f"{len(lista)} URLs é lote demais - submetendo só a primeira.")
        lista = lista[:1]

    corpo = json.dumps(montar_payload(cfg, chave, lista)).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=corpo,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": AGENTE},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"IndexNow respondeu {r.status} para {len(lista)} URLs.")
    except urllib.error.HTTPError as e:
        # 422 costuma ser chave não encontrada no domínio. Não é motivo para
        # derrubar o workflow: o site já foi publicado.
        print(f"IndexNow devolveu {e.code}: {e.reason}", file=sys.stderr)
        detalhe = _corpo_do_erro(e)
        if detalhe:
            print(f"Corpo da resposta: {detalhe}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"IndexNow inacessível: {e.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
