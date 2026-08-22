"""
Coletor do Catálogo de Produtos do Portal Único (Siscomex).

Baixa a relação publica de atributos por NCM, extrai as viradas agendadas
- atributos hoje opcionais com data marcada para virar obrigatórios - e
grava um snapshot diário enxuto.

Regra do produto:
    obrigatorio == false AND dataFimVigencia >= hoje (America/Sao_Paulo)

Sem dependencias externas. Somente biblioteca padrao.
"""

import collections
import hashlib
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

# O ?perfil=PUBLICO é obrigatório: sem ele o servidor devolve 307 e, se o
# redirect não for seguido, 304 bytes de HTML no lugar do ZIP.
URL = ("https://portalunico.siscomex.gov.br/cadatributos/api"
       "/atributo-ncm/download/json?perfil=PUBLICO")

# Identifica o coletor para quem olhar o log do servidor. Sem isso vai
# "Python-urllib/3.x", que é o primeiro padrão que um WAF corta.
AGENTE = ("SentinelaDoCatalogo/1.0 "
          "(+https://github.com/viniciuscaetanosr/sentinela-catalogo)")

RAIZ = os.path.dirname(os.path.abspath(__file__))
DIR_DADOS = os.path.join(RAIZ, "dados")
DIR_HISTORICO = os.path.join(DIR_DADOS, "historico")
ARQ_ULTIMO = os.path.join(DIR_DADOS, "ultimo.json")
ARQ_ATRIBUTOS = os.path.join(DIR_DADOS, "atributos.json")
# Mapa completo NCM -> atributos. NAO é versionado (ver .gitignore): são
# ~3 MB que mudam todo dia e o gerador consome no mesmo run do CI.
ARQ_COMPLETO = os.path.join(DIR_DADOS, "completo.json")

FUSO = ZoneInfo("America/Sao_Paulo")

# Versão do formato do snapshot. bloco_historico() lê arquivos escritos por
# versões antigas do código; sem esta marca não há como saber o que esperar.
SCHEMA = 1

# Campos que mudam a cada execução sem que o dado tenha mudado. Ficam fora
# da comparação que decide se vale reescrever o arquivo - senão todo run
# produz um commit que não carrega informação nenhuma.
VOLATEIS = ("coletado_em", "bytes_zip", "disposition")


def _sem_volateis(texto):
    """Ignora as linhas voláteis ao comparar - só o conteúdo importa."""
    nl = chr(10)
    return nl.join(l for l in texto.split(nl)
                   if not any('"' + c + '"' in l for c in VOLATEIS))


def hoje_br():
    """A data de referência da regra, no fuso de Brasilia.

    O arquivo é regenerado pelo servidor por volta de 00:0x horário de
    Brasilia. Usar UTC deslocaria a janela perto da meia-noite.
    """
    return datetime.now(FUSO).date()


def slug(texto):
    """Nome de órgão -> pedaço de URL. Dobra o acento em vez de descartá-lo.

    Sem a dobra, "Sem órgão declarado" virava "sem-rg-o-declarado": o
    regex [^a-z0-9] come o "ó" e o "ã" inteiros.
    """
    plano = unicodedata.normalize("NFKD", texto or "")
    plano = plano.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", plano.lower()).strip("-") or "sem-nome"


def _gravar_json(caminho, dados):
    """Escrita atômica.

    open(..., "w") trunca antes de escrever: um job cancelado no meio deixa
    o arquivo pela metade, e aí TODA execução seguinte quebra no json.load.
    """
    temporario = caminho + ".tmp"
    with open(temporario, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(temporario, caminho)


def baixar(url=URL, timeout=90, tentativas=3):
    """Devolve (bytes_do_json, metadados_http).

    Com retry: uma falha transitória às 09:00 UTC custa o dia inteiro, e o
    endpoint ignora ?data= - não existe como buscar o arquivo de ontem.
    Não repete em 4xx: 406 é header errado, e insistir não conserta.
    """
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            return _baixar_uma_vez(url, timeout)
        except urllib.error.HTTPError as e:
            ultimo_erro = e
            if e.code < 500:
                raise
        except (urllib.error.URLError, zipfile.BadZipFile, TimeoutError) as e:
            ultimo_erro = e
        if tentativa < tentativas:
            espera = 2 ** tentativa
            print(f"tentativa {tentativa}/{tentativas} falhou ({ultimo_erro}); "
                  f"nova tentativa em {espera}s", file=sys.stderr)
            time.sleep(espera)
    raise ultimo_erro


def _baixar_uma_vez(url, timeout):
    # Não enviar Accept: application/json - o endpoint devolve 406.
    # Ele só serve application/zip.
    req = urllib.request.Request(url, headers={"Accept": "*/*",
                                               "User-Agent": AGENTE})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        bruto = r.read()
        meta = {
            "status": r.status,
            "content_type": r.headers.get("Content-Type"),
            "bytes_zip": len(bruto),
            "disposition": r.headers.get("Content-Disposition"),
        }

    # Página de manutenção servida com 200 é a falha que passa despercebida:
    # sem esta checagem ela vira BadZipFile lá embaixo, sem explicação.
    tipo = (meta["content_type"] or "").lower()
    if "zip" not in tipo:
        raise RuntimeError("esperava application/zip, veio "
                           f"{tipo!r} ({len(bruto)} bytes)")

    z = zipfile.ZipFile(io.BytesIO(bruto))
    nomes = z.namelist()
    if len(nomes) != 1:
        raise RuntimeError(f"ZIP deveria ter 1 arquivo, tem {len(nomes)}: {nomes}")

    # O nome muda todo dia (ATRIBUTOS_POR_NCM_AAAA_MM_DD.json). Nunca hardcode.
    meta["arquivo_interno"] = nomes[0]
    conteudo = z.read(nomes[0])
    meta["bytes_json"] = len(conteudo)

    # Os bytes do ZIP mudam a cada requisição porque o mtime interno é o
    # instante da geração. Só o JSON descompactado tem hash estavel.
    meta["sha256_json"] = hashlib.sha256(conteudo).hexdigest()
    return conteudo, meta


def carregar(conteudo):
    """JSON -> dict. UTF-8 sem BOM, confirmado."""
    return json.loads(conteudo.decode("utf-8"))


def _fim_vigencia(vinculo):
    """Le dataFimVigencia tolerando as DUAS convenções de ausência.

    Em listaAtributos a chave é omitida quando não há fim.
    Em detalhesAtributos ela vem como string vazia.
    Nunca comparar "" como data: "" < qualquer data é True.

    Valida a data de verdade, não só o formato: um "2026-02-30" passava pelo
    regex, era gravado, e só quebrava depois no gerador - com o dado ruim já
    commitado e toda execução seguinte falhando.
    """
    valor = vinculo.get("dataFimVigencia")
    if not valor:
        return None
    try:
        date.fromisoformat(valor)
    except (ValueError, TypeError):
        raise ValueError(f"dataFimVigencia invalida: {valor!r}")
    return valor


def lista_ncms(dados):
    """listaNcm sem duplicata e sem registro capenga.

    NCM repetida gerava duas fichas para o mesmo código - a mesma página
    escrita duas vezes e o total_ncms contado em dobro.
    """
    vistas = {}
    for ncm in dados.get("listaNcm", []):
        codigo = ncm.get("codigoNcm")
        if not codigo:
            continue
        vistas.setdefault(codigo, ncm)
    return list(vistas.values())


def vinculos_de(ncm):
    """Os vínculos de uma NCM, descartando os que não têm código."""
    return [v for v in ncm.get("listaAtributos", []) if v.get("codigo")]


def dicionario_atributos(dados):
    """código -> detalhe do atributo (nome, tipo, domínio, órgãos)."""
    return {a["codigo"]: a for a in dados.get("detalhesAtributos", [])
            if a.get("codigo")}


def nome_de(detalhe):
    return detalhe.get("nomeApresentacao") or detalhe.get("nome")


def viradas(dados, referencia=None):
    """As viradas agendadas, ordenadas pela data em que passam a valer.

    O corte é >= e não >: no dia exato da virada o vínculo tem de continuar
    aparecendo. É o dia mais acionável para quem mantém o catálogo, e era
    justamente o que sumia da página - a cópia "É hoje." nunca rodava.
    """
    ref = (referencia or hoje_br()).isoformat()
    dic = dicionario_atributos(dados)
    achados = []

    for ncm in lista_ncms(dados):
        codigo_ncm = ncm["codigoNcm"]
        for vinculo in vinculos_de(ncm):
            fim = _fim_vigencia(vinculo)
            if fim is None or fim < ref:
                continue
            # A cláusula obrigatorio == false não filtra nada hoje: todos os
            # vínculos com dataFimVigencia são opcionais. Mantida porque passa
            # a filtrar no dia em que o órgão publicar uma virada já obrigatória.
            if vinculo.get("obrigatorio") is not False:
                continue

            detalhe = dic.get(vinculo["codigo"], {})
            achados.append({
                "ncm": codigo_ncm,
                "atributo": vinculo["codigo"],
                "vira_obrigatorio_em": fim,
                "vigente_desde": vinculo.get("dataInicioVigencia"),
                "modalidade": vinculo.get("modalidade"),
                "nome": nome_de(detalhe),
                "orgaos": detalhe.get("orgaos") or [],
                "forma_preenchimento": detalhe.get("formaPreenchimento"),
            })

    achados.sort(key=lambda x: (x["vira_obrigatorio_em"], x["ncm"], x["atributo"]))
    return achados


MAX_DOMINIO = 120
# Domínio inteiro dos atributos que NÃO ganham página própria: ele passa a
# ser exibido dentro da página da NCM, e precisa caber lá.
MAX_DOMINIO_INLINE = 30


def detalhe_publico(detalhe):
    """Os campos do dicionário de atributos que a página usa."""
    if not detalhe:
        return {}
    dominio = detalhe.get("dominio") or []
    return {
        "dominio_total": len(dominio),
        "codigo": detalhe.get("codigo"),
        "nome": nome_de(detalhe),
        "definicao": detalhe.get("definicao"),
        "orientacao": detalhe.get("orientacaoPreenchimento"),
        "forma_preenchimento": detalhe.get("formaPreenchimento"),
        "orgaos": detalhe.get("orgaos") or [],
        "multivalorado": detalhe.get("multivalorado"),
        # dominio[].codigo é string e preserva zero a esquerda ("01").
        # Truncado: ATT_14500 ("Município do destino final") tem 5.570 opções,
        # que renderizariam uma página de 187 mil caracteres.
        "dominio": [
            {"codigo": d.get("codigo"), "descricao": d.get("descricao")}
            for d in dominio[:MAX_DOMINIO]
        ],
    }


def ncms_afetadas(dados, lista_viradas):
    """Ficha completa de cada NCM que tem virada agendada.

    Mostra TODOS os atributos da NCM, marcando quais viram obrigatórios -
    o importador precisa ver o contexto, não só a linha que muda.
    """
    alvo = {v["ncm"] for v in lista_viradas}
    virando = {(v["ncm"], v["atributo"]): v["vira_obrigatorio_em"] for v in lista_viradas}
    dic = dicionario_atributos(dados)
    saida = []

    for ncm in lista_ncms(dados):
        codigo = ncm["codigoNcm"]
        if codigo not in alvo:
            continue
        atributos = []
        for vinculo in vinculos_de(ncm):
            detalhe = dic.get(vinculo["codigo"], {})
            atributos.append({
                "codigo": vinculo["codigo"],
                "nome": nome_de(detalhe),
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


# Números longos dentro da definição são o código da NCM. Trocados por "#"
# para que "...destaques de exportação da NCM 01012100" e a mesma frase com
# outra NCM contem como a MESMA prosa - que é o que elas são.
RE_NUMERO_LONGO = re.compile(r"\d{4,}")


def assinatura_prosa(detalhe):
    """O texto que a página mostraria, sem o número da NCM."""
    if not detalhe:
        return ("", "", "")
    return (
        (nome_de(detalhe) or "").strip(),
        RE_NUMERO_LONGO.sub("#", (detalhe.get("definicao") or "").strip()),
        (detalhe.get("orientacaoPreenchimento") or "").strip(),
    )


def merece_pagina(detalhe, total_ncms, repeticoes_da_prosa):
    """O filtro de qualidade das páginas por atributo.

    A versão anterior exigia apenas que EXISTISSE prosa, e por isso não fazia
    o que o próprio comentário dela prometia: "Escolher apenas um Destaque"
    são 31 caracteres idênticos em 586 registros, e passavam os 586.

    O corte agora é estrutural, não estilístico: um atributo que vale para
    UMA única NCM e cuja prosa é boilerplate repetido não tem o que dizer
    numa URL própria - tudo o que ele mostra já aparece na página daquela
    NCM, agora que existe uma página para cada NCM.
    """
    if not detalhe:
        return False
    if total_ncms == 1 and repeticoes_da_prosa > 1:
        return False
    return True


def indice_por_atributo(dados):
    """código do atributo -> lista de NCMs vinculadas."""
    por_atributo = {}
    for ncm in lista_ncms(dados):
        for vinculo in vinculos_de(ncm):
            por_atributo.setdefault(vinculo["codigo"], []).append(ncm["codigoNcm"])
    return por_atributo


def atributos_publicaveis(dados, lista_viradas, max_ncms=60):
    """Todo atributo que merece página própria.

    Entram, sempre: os das viradas e os citados por NCM afetada - senão as
    páginas daquelas NCMs linkariam para o vazio. Os demais entram pelo
    filtro de qualidade.
    """
    dic = dicionario_atributos(dados)
    por_atributo = indice_por_atributo(dados)
    repeticoes = collections.Counter(assinatura_prosa(d) for d in dic.values())

    nas_viradas = {v["atributo"] for v in lista_viradas}
    obrigatorios = set(nas_viradas)
    alvo = {v["ncm"] for v in lista_viradas}
    for ncm in lista_ncms(dados):
        if ncm["codigoNcm"] in alvo:
            obrigatorios |= {v["codigo"] for v in vinculos_de(ncm)}

    saida = []
    for codigo, ncms in sorted(por_atributo.items()):
        detalhe = dic.get(codigo)
        if codigo not in obrigatorios:
            if not merece_pagina(detalhe, len(ncms),
                                 repeticoes[assinatura_prosa(detalhe)]):
                continue
        item = detalhe_publico(detalhe)
        item.update({
            "codigo": codigo,
            "total_ncms": len(ncms),
            "ncms": sorted(ncms)[:max_ncms],
            "nas_viradas": codigo in nas_viradas,
        })
        saida.append(item)
    return saida


def mapa_completo(dados, com_pagina):
    """NCM -> atributos, para gerar uma página por NCM.

    Não é versionado: são ~73 mil vínculos que mudam todo dia. O gerador
    consome no mesmo run do CI e o arquivo morre com o runner.

    Quem NÃO tem página própria viaja com orientação e domínio embutidos -
    o conteúdo não some, muda de lugar: passa a ser exibido dentro da
    página da NCM.
    """
    dic = dicionario_atributos(dados)
    ncms = {}
    usados = set()
    for ncm in lista_ncms(dados):
        vinculos = vinculos_de(ncm)
        if not vinculos:
            continue
        ncms[ncm["codigoNcm"]] = [
            [v["codigo"], v.get("obrigatorio"), v.get("modalidade")]
            for v in vinculos
        ]
        usados |= {v["codigo"] for v in vinculos}

    atributos = {}
    for codigo in sorted(usados):
        detalhe = dic.get(codigo) or {}
        item = {"n": nome_de(detalhe), "o": detalhe.get("orgaos") or []}
        if codigo not in com_pagina:
            orientacao = (detalhe.get("orientacaoPreenchimento") or "").strip()
            dominio = detalhe.get("dominio") or []
            if orientacao:
                item["t"] = orientacao
            if dominio:
                item["d"] = [[d.get("codigo"), d.get("descricao")]
                             for d in dominio[:MAX_DOMINIO_INLINE]]
                item["dt"] = len(dominio)
        atributos[codigo] = item

    return {"versao": dados.get("versao"), "ncms": ncms, "atributos": atributos}


def orgaos(atributos):
    """Um agrupamento por órgão anuente.

    Cria um eixo de navegação e de consulta novo ("atributos anvisa duimp") e
    evita um indice único com centenas de itens.
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
    usados = set()
    for orgao, lista in sorted(por_orgao.items()):
        # Dois órgãos que só diferem na pontuação ("MIN.DEFESA" e "MIN DEFESA")
        # colidiriam no mesmo slug e uma página sobrescreveria a outra.
        base = slug(orgao)
        s, n = base, 2
        while s in usados:
            s, n = f"{base}-{n}", n + 1
        usados.add(s)
        lista.sort(key=lambda x: (-x["total_ncms"], x["codigo"]))
        saida.append({
            "orgao": orgao,
            "slug": s,
            "total_atributos": len(lista),
            "atributos": lista,
        })
    saida.sort(key=lambda x: -x["total_atributos"])
    return saida


def contagens(dados, referencia=None):
    """Números de controle. Alimentam o portão de sanidade."""
    ncms = lista_ncms(dados)
    brutos = [v for n in ncms for v in n.get("listaAtributos", [])]
    vinculos = [v for v in brutos if v.get("codigo")]
    hoje = (referencia or hoje_br()).isoformat()
    return {
        "versao": dados.get("versao"),
        "ncms": len(ncms),
        "ncms_sem_atributo": sum(1 for n in ncms if not vinculos_de(n)),
        "atributos_distintos": len({v["codigo"] for v in vinculos}),
        "detalhes_atributos": len(dicionario_atributos(dados)),
        "vinculos": len(vinculos),
        "descartados": len(brutos) - len(vinculos),
        "obrigatorios": sum(1 for v in vinculos if v.get("obrigatorio") is True),
        "com_fim_vigencia": sum(1 for v in vinculos if _fim_vigencia(v)),
        "inicio_vigencia_futuro": sum(
            1 for v in vinculos if (v.get("dataInicioVigencia") or "") > hoje
        ),
    }


# Piso absoluto, para a primeira execução, quando não há snapshot anterior
# com que comparar. Bem abaixo do real: serve para pegar catástrofe, não
# para pegar variação.
PISO = {"ncms": 5000, "vinculos": 30000, "atributos_distintos": 500}
# Quanto a base pode encolher de um dia para o outro antes de virar suspeita.
QUEDA_MAXIMA = 0.10


def conferir_sanidade(atual, anterior=None):
    """Levanta se a colheita estiver degenerada. Roda ANTES de gravar.

    Sem isto, um ZIP válido com listaNcm vazia derruba atributos.json de
    1 MB para 85 bytes, o site de 918 páginas para 5, e o workflow commita e
    publica tudo com exit 0. O endpoint ignora ?data=: o dia não volta.
    """
    problemas = []
    if not atual.get("versao"):
        problemas.append("versao ausente ou vazia")
    if atual.get("detalhes_atributos", 0) == 0:
        problemas.append("detalhesAtributos vazio")

    for chave, piso in PISO.items():
        if atual.get(chave, 0) < piso:
            problemas.append(f"{chave}={atual.get(chave)} abaixo do piso {piso}")

    if anterior:
        for chave in ("ncms", "vinculos", "atributos_distintos"):
            antes, agora = anterior.get(chave), atual.get(chave)
            if not antes or agora is None:
                continue
            if agora < antes * (1 - QUEDA_MAXIMA):
                problemas.append(
                    f"{chave} caiu de {antes} para {agora} "
                    f"(mais de {QUEDA_MAXIMA:.0%})")

    if problemas:
        raise RuntimeError("colheita degenerada, nada foi gravado: "
                           + "; ".join(problemas))


def contagens_anteriores():
    if not os.path.exists(ARQ_ULTIMO):
        return None
    try:
        with open(ARQ_ULTIMO, encoding="utf-8") as f:
            return json.load(f).get("contagens")
    except (ValueError, OSError):
        return None


def _reescrever_se_mudou(caminho, corpo):
    """Grava só quando o conteúdo mudou de verdade.

    Devolve True se escreveu. A comparação ignora os campos voláteis: sem
    isso o carimbo de hora garante diff todo dia e o "Nada mudou hoje." do
    workflow nunca dispara - 16 dos 18 primeiros commits do projeto não
    carregavam nada além do relógio.
    """
    anterior = ""
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            anterior = f.read()
    if _sem_volateis(corpo) == _sem_volateis(anterior.rstrip(chr(10))):
        return False
    temporario = caminho + ".tmp"
    with open(temporario, "w", encoding="utf-8", newline="\n") as f:
        f.write(corpo + chr(10))
    os.replace(temporario, caminho)
    return True


def coletar():
    """Baixa, apura e grava o snapshot do dia. Devolve o snapshot."""
    conteudo, meta = baixar()
    dados = carregar(conteudo)
    ref = hoje_br()

    # Portão de sanidade: antes de qualquer escrita.
    c = contagens(dados, ref)
    conferir_sanidade(c, contagens_anteriores())

    vs = viradas(dados, ref)
    snapshot = {
        "schema": SCHEMA,
        "coletado_em": datetime.now(FUSO).isoformat(timespec="seconds"),
        "data_referencia": ref.isoformat(),
        "fonte": URL,
        "http": meta,
        "contagens": c,
        "viradas": vs,
        "ncms_afetadas": ncms_afetadas(dados, vs),
    }

    # O detalhe dos atributos sai do snapshot diário: são centenas de itens e
    # eles quase nunca mudam. Reescrever só quando o conteúdo muda mantem o
    # commit diário pequeno.
    publicaveis = atributos_publicaveis(dados, vs)
    catalogo = {
        "versao": dados.get("versao"),
        "atualizado_em": ref.isoformat(),
        "atributos": publicaveis,
        "orgaos": orgaos(publicaveis),
    }
    corpo = json.dumps(catalogo, ensure_ascii=False, indent=1, sort_keys=True)
    snapshot["catalogo_reescrito"] = _reescrever_se_mudou(ARQ_ATRIBUTOS, corpo)
    snapshot["atributos_publicaveis"] = len(publicaveis)
    snapshot["orgaos"] = len(catalogo["orgaos"])

    com_pagina = {a["codigo"] for a in publicaveis}
    _gravar_json(ARQ_COMPLETO, mapa_completo(dados, com_pagina))

    os.makedirs(DIR_HISTORICO, exist_ok=True)
    corpo_snapshot = json.dumps(snapshot, ensure_ascii=False, indent=1)
    caminho = os.path.join(DIR_HISTORICO, f"{ref.isoformat()}.json")
    escritos = [_reescrever_se_mudou(caminho, corpo_snapshot),
                _reescrever_se_mudou(ARQ_ULTIMO, corpo_snapshot)]
    snapshot["gravado"] = any(escritos)
    return snapshot


# Invariantes de FORMA: valem para sempre, independentes de quanto o arquivo
# cresceu. As de MAGNITUDE ficam em conferir_sanidade(), com base rolante -
# uma tabela de números congelados vira ruído em dois dias.
def invariantes(c):
    return [
        ("versao e string", isinstance(c.get("versao"), str)),
        ("detalhes == distintos",
         c.get("detalhes_atributos") == c.get("atributos_distintos")),
        ("nenhum registro descartado", c.get("descartados") == 0),
        ("nenhum inicio de vigencia futuro",
         c.get("inicio_vigencia_futuro") == 0),
    ]


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
    except (RuntimeError, ValueError, zipfile.BadZipFile) as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1

    c = snapshot["contagens"]
    v = snapshot["viradas"]
    print(f"Coletado em {snapshot['coletado_em']} | versao {c['versao']} | "
          f"{snapshot['http']['bytes_zip']} bytes zip -> "
          f"{snapshot['http']['bytes_json']} bytes json")
    print(f"{c['ncms']} NCMs, {c['vinculos']} vinculos, {c['obrigatorios']} obrigatorios")
    print()
    print("Invariantes:")
    for nome, ok in invariantes(c):
        print(f"  {'ok  ' if ok else 'FALHA'} {nome}")
    print()
    print(f"{snapshot.get('atributos_publicaveis', 0)} atributos publicaveis em "
          f"{snapshot.get('orgaos', 0)} orgaos"
          f"{' (catalogo reescrito)' if snapshot.get('catalogo_reescrito') else ' (catalogo inalterado)'}")
    if not snapshot.get("gravado"):
        print("Nada mudou hoje: snapshot identico ao anterior, nada reescrito.")
    print()
    print(f"{len(v)} viradas agendadas (obrigatorio=false E dataFimVigencia >= hoje):")
    for x in v:
        orgao = "/".join(x["orgaos"]) or "-"
        print(f"  {x['vira_obrigatorio_em']}  {x['ncm']}  {x['atributo']:<12} "
              f"{orgao:<10} {x['nome'] or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
