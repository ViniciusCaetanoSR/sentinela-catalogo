"""
Coletor do Catalogo de Produtos do Portal Unico (Siscomex).

Baixa a relacao publica de atributos por NCM, extrai as viradas agendadas
- atributos hoje opcionais com data marcada para virar obrigatorios - e
grava um snapshot diario enxuto.

Regra do produto:
    obrigatorio == false AND dataFimVigencia > hoje (America/Sao_Paulo)

Sem dependencias externas. Somente biblioteca padrao.
"""

import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

# O ?perfil=PUBLICO e obrigatorio: sem ele o servidor devolve 307 e, se o
# redirect nao for seguido, 304 bytes de HTML no lugar do ZIP.
URL = ("https://portalunico.siscomex.gov.br/cadatributos/api"
       "/atributo-ncm/download/json?perfil=PUBLICO")

RAIZ = os.path.dirname(os.path.abspath(__file__))
DIR_DADOS = os.path.join(RAIZ, "dados")
DIR_HISTORICO = os.path.join(DIR_DADOS, "historico")
ARQ_ULTIMO = os.path.join(DIR_DADOS, "ultimo.json")
ARQ_ATRIBUTOS = os.path.join(DIR_DADOS, "atributos.json")

FUSO = ZoneInfo("America/Sao_Paulo")
RE_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _sem_data(texto):
    """Ignora a linha de data ao comparar - so o conteudo importa."""
    nl = chr(10)
    return nl.join(l for l in texto.split(nl) if "atualizado_em" not in l)


def hoje_br():
    """A data de referencia da regra, no fuso de Brasilia.

    O arquivo e regenerado pelo servidor por volta de 00:0x horario de
    Brasilia. Usar UTC deslocaria a janela perto da meia-noite.
    """
    return datetime.now(FUSO).date()


def baixar(url=URL, timeout=90):
    """Devolve (bytes_do_json, metadados_http)."""
    # Nao enviar Accept: application/json - o endpoint devolve 406.
    # Ele so serve application/zip.
    req = urllib.request.Request(url, headers={"Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        bruto = r.read()
        meta = {
            "status": r.status,
            "content_type": r.headers.get("Content-Type"),
            "bytes_zip": len(bruto),
            "disposition": r.headers.get("Content-Disposition"),
        }

    z = zipfile.ZipFile(io.BytesIO(bruto))
    nomes = z.namelist()
    if len(nomes) != 1:
        raise RuntimeError(f"ZIP deveria ter 1 arquivo, tem {len(nomes)}: {nomes}")

    # O nome muda todo dia (ATRIBUTOS_POR_NCM_AAAA_MM_DD.json). Nunca hardcode.
    meta["arquivo_interno"] = nomes[0]
    conteudo = z.read(nomes[0])
    meta["bytes_json"] = len(conteudo)

    # Os bytes do ZIP mudam a cada requisicao porque o mtime interno e o
    # instante da geracao. So o JSON descompactado tem hash estavel.
    meta["sha256_json"] = hashlib.sha256(conteudo).hexdigest()
    return conteudo, meta


def carregar(conteudo):
    """JSON -> dict. UTF-8 sem BOM, confirmado."""
    return json.loads(conteudo.decode("utf-8"))


def _fim_vigencia(vinculo):
    """Le dataFimVigencia tolerando as DUAS convencoes de ausencia.

    Em listaAtributos a chave e omitida quando nao ha fim.
    Em detalhesAtributos ela vem como string vazia.
    Nunca comparar "" como data: "" < qualquer data e True.
    """
    valor = vinculo.get("dataFimVigencia")
    if not valor:
        return None
    if not RE_DATA.match(valor):
        raise ValueError(f"dataFimVigencia fora do formato AAAA-MM-DD: {valor!r}")
    return valor


def dicionario_atributos(dados):
    """codigo -> detalhe do atributo (nome, tipo, dominio, orgaos)."""
    return {a["codigo"]: a for a in dados.get("detalhesAtributos", [])}


def viradas(dados, referencia=None):
    """As viradas agendadas, ordenadas pela data em que passam a valer."""
    ref = (referencia or hoje_br()).isoformat()
    dic = dicionario_atributos(dados)
    achados = []

    for ncm in dados.get("listaNcm", []):
        codigo_ncm = ncm.get("codigoNcm")
        for vinculo in ncm.get("listaAtributos", []):
            fim = _fim_vigencia(vinculo)
            if fim is None or fim <= ref:
                continue
            # A clausula obrigatorio == false nao filtra nada hoje: todos os
            # vinculos com dataFimVigencia sao opcionais. Mantida porque passa
            # a filtrar no dia em que o orgao publicar uma virada ja obrigatoria.
            if vinculo.get("obrigatorio") is not False:
                continue

            detalhe = dic.get(vinculo["codigo"], {})
            achados.append({
                "ncm": codigo_ncm,
                "atributo": vinculo["codigo"],
                "vira_obrigatorio_em": fim,
                "vigente_desde": vinculo.get("dataInicioVigencia"),
                "modalidade": vinculo.get("modalidade"),
                "nome": detalhe.get("nomeApresentacao") or detalhe.get("nome"),
                "orgaos": detalhe.get("orgaos") or [],
                "forma_preenchimento": detalhe.get("formaPreenchimento"),
            })

    achados.sort(key=lambda x: (x["vira_obrigatorio_em"], x["ncm"], x["atributo"]))
    return achados


MAX_DOMINIO = 120


def detalhe_publico(detalhe):
    """Os campos do dicionario de atributos que a pagina usa."""
    if not detalhe:
        return {}
    dominio = detalhe.get("dominio") or []
    return {
        "dominio_total": len(dominio),
        "codigo": detalhe.get("codigo"),
        "nome": detalhe.get("nomeApresentacao") or detalhe.get("nome"),
        "definicao": detalhe.get("definicao"),
        "orientacao": detalhe.get("orientacaoPreenchimento"),
        "forma_preenchimento": detalhe.get("formaPreenchimento"),
        "orgaos": detalhe.get("orgaos") or [],
        "multivalorado": detalhe.get("multivalorado"),
        # dominio[].codigo e string e preserva zero a esquerda ("01").
        # Truncado: ATT_14500 ("Municipio do destino final") tem 5.570 opcoes,
        # que renderizariam uma pagina de 187 mil caracteres.
        "dominio": [
            {"codigo": d.get("codigo"), "descricao": d.get("descricao")}
            for d in dominio[:MAX_DOMINIO]
        ],
    }


def ncms_afetadas(dados, lista_viradas):
    """Ficha completa de cada NCM que tem virada agendada.

    Mostra TODOS os atributos da NCM, marcando quais viram obrigatorios -
    o importador precisa ver o contexto, nao so a linha que muda.
    """
    alvo = {v["ncm"] for v in lista_viradas}
    virando = {(v["ncm"], v["atributo"]): v["vira_obrigatorio_em"] for v in lista_viradas}
    dic = dicionario_atributos(dados)
    saida = []

    for ncm in dados.get("listaNcm", []):
        codigo = ncm.get("codigoNcm")
        if codigo not in alvo:
            continue
        atributos = []
        for vinculo in ncm.get("listaAtributos", []):
            detalhe = dic.get(vinculo["codigo"], {})
            atributos.append({
                "codigo": vinculo["codigo"],
                "nome": detalhe.get("nomeApresentacao") or detalhe.get("nome"),
                "obrigatorio": vinculo.get("obrigatorio"),
                "modalidade": vinculo.get("modalidade"),
                "orgaos": detalhe.get("orgaos") or [],
                "vira_obrigatorio_em": virando.get((codigo, vinculo["codigo"])),
            })
        atributos.sort(key=lambda a: (a["vira_obrigatorio_em"] is None,
                                      not a["obrigatorio"], a["codigo"]))
        saida.append({"ncm": codigo, "atributos": atributos})

    saida.sort(key=lambda x: x["ncm"])
    return saida


def tem_conteudo(detalhe, total_ncms):
    """O filtro de qualidade das paginas por atributo.

    Exige conteudo REAL, nao a mera existencia do registro: sem isso
    entrariam centenas de paginas do tipo "Detalhamento" com duas linhas e
    uma NCM - exatamente o padrao de conteudo fino que penaliza o site todo.
    """
    if not detalhe:
        return False
    # Prosa oficial e o sinal forte: 823 atributos tem orientacao ou definicao.
    # Sem prosa, so entra quem tem lista de opcoes substantiva OU alcance largo -
    # um atributo em 20+ NCMs e encontrado por muita gente mesmo sem texto.
    return bool(
        (detalhe.get("orientacaoPreenchimento") or "").strip()
        or (detalhe.get("definicao") or "").strip()
        or len(detalhe.get("dominio") or []) >= 8
        or total_ncms >= 20
    )


def indice_por_atributo(dados):
    """codigo do atributo -> lista de NCMs vinculadas."""
    por_atributo = {}
    for ncm in dados.get("listaNcm", []):
        for vinculo in ncm.get("listaAtributos", []):
            por_atributo.setdefault(vinculo["codigo"], []).append(ncm["codigoNcm"])
    return por_atributo


def atributos_publicaveis(dados, lista_viradas, max_ncms=60):
    """Todo atributo que merece pagina propria.

    Entram, sempre: os das viradas e os citados por NCM afetada - senao as
    paginas daquelas NCMs linkariam para o vazio. Os demais entram pelo
    filtro de qualidade.
    """
    dic = dicionario_atributos(dados)
    por_atributo = indice_por_atributo(dados)

    obrigatorios = {v["atributo"] for v in lista_viradas}
    alvo = {v["ncm"] for v in lista_viradas}
    for ncm in dados.get("listaNcm", []):
        if ncm.get("codigoNcm") in alvo:
            obrigatorios |= {v["codigo"] for v in ncm.get("listaAtributos", [])}

    saida = []
    for codigo, ncms in sorted(por_atributo.items()):
        detalhe = dic.get(codigo)
        if codigo not in obrigatorios and not tem_conteudo(detalhe, len(ncms)):
            continue
        item = detalhe_publico(detalhe)
        item.update({
            "codigo": codigo,
            "total_ncms": len(ncms),
            "ncms": sorted(ncms)[:max_ncms],
            "nas_viradas": codigo in {v["atributo"] for v in lista_viradas},
        })
        saida.append(item)
    return saida


def orgaos(atributos):
    """Um agrupamento por orgao anuente.

    Cria um eixo de navegacao e de consulta novo ("atributos anvisa duimp") e
    evita um indice unico com centenas de itens.
    """
    por_orgao = {}
    for a in atributos:
        for orgao in a.get("orgaos") or ["Sem órgão declarado"]:
            por_orgao.setdefault(orgao, []).append({
                "codigo": a["codigo"],
                "nome": a.get("nome"),
                "forma_preenchimento": a.get("forma_preenchimento"),
                "total_ncms": a.get("total_ncms", 0),
                "nas_viradas": a.get("nas_viradas", False),
            })
    saida = []
    for orgao, lista in sorted(por_orgao.items()):
        lista.sort(key=lambda x: (-x["total_ncms"], x["codigo"]))
        saida.append({
            "orgao": orgao,
            "slug": re.sub(r"[^a-z0-9]+", "-", orgao.lower()).strip("-"),
            "total_atributos": len(lista),
            "atributos": lista,
        })
    saida.sort(key=lambda x: -x["total_atributos"])
    return saida


def contagens(dados):
    """Numeros de controle. Servem de teste de aceitacao do coletor."""
    ncms = dados.get("listaNcm", [])
    vinculos = [v for n in ncms for v in n.get("listaAtributos", [])]
    hoje = hoje_br().isoformat()
    return {
        "versao": dados.get("versao"),
        "ncms": len(ncms),
        "ncms_sem_atributo": sum(1 for n in ncms if not n.get("listaAtributos")),
        "atributos_distintos": len({v["codigo"] for v in vinculos}),
        "detalhes_atributos": len(dados.get("detalhesAtributos", [])),
        "vinculos": len(vinculos),
        "obrigatorios": sum(1 for v in vinculos if v.get("obrigatorio") is True),
        "com_fim_vigencia": sum(1 for v in vinculos if _fim_vigencia(v)),
        "inicio_vigencia_futuro": sum(
            1 for v in vinculos if (v.get("dataInicioVigencia") or "") > hoje
        ),
    }


def coletar():
    """Baixa, apura e grava o snapshot do dia. Devolve o snapshot."""
    conteudo, meta = baixar()
    dados = carregar(conteudo)
    ref = hoje_br()
    vs = viradas(dados, ref)

    snapshot = {
        "coletado_em": datetime.now(FUSO).isoformat(timespec="seconds"),
        "data_referencia": ref.isoformat(),
        "fonte": URL,
        "http": meta,
        "contagens": contagens(dados),
        "viradas": vs,
        "ncms_afetadas": ncms_afetadas(dados, vs),
    }

    # O detalhe dos atributos sai do snapshot diario: sao centenas de itens e
    # eles quase nunca mudam. Reescrever so quando o conteudo muda mantem o
    # commit diario pequeno.
    publicaveis = atributos_publicaveis(dados, vs)
    catalogo = {
        "versao": dados.get("versao"),
        "atualizado_em": ref.isoformat(),
        "atributos": publicaveis,
        "orgaos": orgaos(publicaveis),
    }
    corpo = json.dumps(catalogo, ensure_ascii=False, indent=1, sort_keys=True)
    anterior = ""
    if os.path.exists(ARQ_ATRIBUTOS):
        with open(ARQ_ATRIBUTOS, encoding="utf-8") as f:
            anterior = f.read()
    if _sem_data(corpo) != _sem_data(anterior):
        with open(ARQ_ATRIBUTOS, "w", encoding="utf-8") as f:
            f.write(corpo + chr(10))
        snapshot["catalogo_reescrito"] = True
    snapshot["atributos_publicaveis"] = len(publicaveis)
    snapshot["orgaos"] = len(catalogo["orgaos"])

    os.makedirs(DIR_HISTORICO, exist_ok=True)
    caminho = os.path.join(DIR_HISTORICO, f"{ref.isoformat()}.json")
    for destino in (caminho, ARQ_ULTIMO):
        with open(destino, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=1)
            f.write("\n")

    return snapshot


ESPERADO = {
    "versao": "345",
    "ncms": 10571,
    "atributos_distintos": 1311,
    "detalhes_atributos": 1311,
    "vinculos": 73246,
    "obrigatorios": 15078,
    "com_fim_vigencia": 14,
    "inicio_vigencia_futuro": 0,
}


def verificar(snapshot):
    """Compara com o que foi medido no reconhecimento de 20/08/2026.

    Divergencia aqui e leitura errada da estrutura, nao dado errado -
    ate que o proprio arquivo mude, o que e esperado com o tempo.
    """
    c = snapshot["contagens"]
    linhas = []
    for chave, esperado in ESPERADO.items():
        obtido = c.get(chave)
        marca = "ok  " if obtido == esperado else "DIFERE"
        linhas.append(f"  {marca} {chave}: {obtido} (referencia 20/08/2026: {esperado})")
    return linhas


def main():
    try:
        snapshot = coletar()
    except urllib.error.HTTPError as e:
        print(f"ERRO HTTP {e.code}: {e.reason}", file=sys.stderr)
        if e.code == 406:
            print("406 = header Accept errado. O endpoint so serve application/zip.",
                  file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"ERRO de rede: {e.reason}", file=sys.stderr)
        return 1

    c = snapshot["contagens"]
    v = snapshot["viradas"]
    print(f"Coletado em {snapshot['coletado_em']} | versao {c['versao']} | "
          f"{snapshot['http']['bytes_zip']} bytes zip -> "
          f"{snapshot['http']['bytes_json']} bytes json")
    print(f"{c['ncms']} NCMs, {c['vinculos']} vinculos, {c['obrigatorios']} obrigatorios")
    print()
    print("Contagens de controle:")
    for linha in verificar(snapshot):
        print(linha)
    print()
    print(f"{snapshot.get('atributos_publicaveis', 0)} atributos publicaveis em "
          f"{snapshot.get('orgaos', 0)} orgaos"
          f"{' (catalogo reescrito)' if snapshot.get('catalogo_reescrito') else ' (catalogo inalterado)'}")
    print()
    print(f"{len(v)} viradas agendadas (obrigatorio=false E dataFimVigencia > hoje):")
    for x in v:
        orgao = "/".join(x["orgaos"]) or "-"
        print(f"  {x['vira_obrigatorio_em']}  {x['ncm']}  {x['atributo']:<12} "
              f"{orgao:<10} {x['nome'] or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
