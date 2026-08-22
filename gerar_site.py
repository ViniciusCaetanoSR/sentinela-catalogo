"""
Gera o site estático a partir de dados/ultimo.json.

Não acessa a rede. Roda depois do coletor, e pode ser rodado sozinho para
recompor o site sem baixar nada de novo.

Saida em site/:
    index.html
    ncm/<NCM>/index.html          uma por NCM do Catálogo
    ncm/capitulo-<NN>/index.html  índice do capítulo NCM
    ncm/index.html                índice dos capítulos
    atributos/<CODIGO>/index.html
    atributos/index.html          indice
    orgaos/<slug>/index.html
    estilo.<hash>.css, app.<hash>.js, og.png, favicon.svg, favicon-32.png,
    apple-touch-icon.png
    sitemap.xml (índice) + sitemap-*.xml, robots.txt, feed.xml, 404.html

Também grava dados/lastmod.json: o hash do conteúdo de cada página e a data
em que ele mudou pela última vez, que alimenta o lastmod do sitemap.
"""

import functools
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import urllib.parse
import zlib
from datetime import date, datetime, timedelta, timezone
from email.utils import formatdate

RAIZ = os.path.dirname(os.path.abspath(__file__))
DIR_TEMPLATES = os.path.join(RAIZ, "templates")
DIR_SITE = os.path.join(RAIZ, "site")
DIR_HISTORICO = os.path.join(RAIZ, "dados", "historico")
ARQ_ULTIMO = os.path.join(RAIZ, "dados", "ultimo.json")
ARQ_CONFIG = os.path.join(RAIZ, "config.json")
ARQ_ATRIBUTOS = os.path.join(RAIZ, "dados", "atributos.json")
ARQ_COMPLETO = os.path.join(RAIZ, "dados", "completo.json")
ARQ_LASTMOD = os.path.join(RAIZ, "dados", "lastmod.json")
DIR_FONTES = os.path.join(RAIZ, "fontes")

MESES = (
    "",
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)

# Usado em dois lugares (página de atributo e página de órgão) e antes estava
# duplicado nos dois, reconstruído a cada iteração do laço interno.
FORMA_PREENCHIMENTO = {
    "LISTA_ESTATICA": "lista de opções",
    "BOOLEANO": "sim ou não",
    "TEXTO": "texto livre",
    "NUMERO_REAL": "número decimal",
    "NUMERO_INTEIRO": "número inteiro",
    "DATA": "data",
}

# Máximo de URLs por arquivo de sitemap. O limite do protocolo é 50 mil;
# 5 mil mantém cada arquivo pequeno e o índice legível.
POR_SITEMAP = 5000
# NCMs por página de capítulo antes de paginar.
POR_PAGINA = 400
# Acima disto o build não mudou conteúdo, foi refeito do zero: não vale
# pingar o IndexNow com a lista inteira.
TETO_INDEXNOW = 200
# Janela do bloco "o que mudou": 30 DIAS, não 30 arquivos.
JANELA_HISTORICO = timedelta(days=30)
# Maior versão de formato de snapshot (coletor.SCHEMA) que este gerador sabe
# ler. Arquivo com schema acima disto é ignorado com aviso - um formato que
# ainda não existe não pode ser interpretado por palpite.
SCHEMA_SUPORTADO = 1


def config():
    padrao = {
        "base_url": "",
        "base_path": "",
        "form_embed_url": "",
        "contato_email": "",
        "goatcounter_code": "",
        "dominio": "",
        "indexnow_key": "",
    }
    if os.path.exists(ARQ_CONFIG):
        with open(ARQ_CONFIG, encoding="utf-8") as f:
            padrao.update(json.load(f))
    return padrao


def esc(texto):
    """Escapa para HTML. Inclui a aspa simples.

    Sem &#39; qualquer valor interpolado dentro de um atributo delimitado por
    aspas simples escapa do atributo.
    """
    if texto is None:
        return ""
    return (
        str(texto)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def prefixo(cfg):
    """Em Pages de repositório de projeto o site vive sob /<repo>/."""
    return cfg.get("base_path", "").rstrip("/")


def url(cfg, caminho):
    """Link interno, já com o prefixo do base_path.

    Substitui a cirurgia de string que rodava sobre o HTML pronto
    (html.replace('href="/', ...)). Aquela versão só pegava aspas duplas, e
    por isso 17 links escritos com aspas simples saíam sem prefixo e davam
    404 - justamente nas dez páginas de atributo com virada agendada.
    """
    return prefixo(cfg) + caminho


def absoluta(cfg, caminho):
    """URL completa, para canonical, og:url, sitemap e feed."""
    base = cfg.get("base_url", "").rstrip("/")
    return base + prefixo(cfg) + caminho


@functools.cache
def template(nome):
    with open(os.path.join(DIR_TEMPLATES, nome), encoding="utf-8") as f:
        return f.read()


RE_CHAVE = re.compile(r"\{\{(\w+)\}\}")


def preencher(texto, valores):
    """Substitui {{chave}} num único passe.

    O laço de str.replace anterior reescaneava a própria saída: um
    "{{formulario}}" que aparecesse na definição oficial de um atributo era
    trocado pelo bloco de captura depois que o corpo já tinha sido inserido.
    """
    return RE_CHAVE.sub(
        lambda m: str(valores[m.group(1)]) if m.group(1) in valores else m.group(0), texto
    )


def br(iso):
    """2026-08-30 -> 30/08/2026"""
    if not iso:
        return ""
    a, m, d = iso.split("-")
    return f"{d}/{m}/{a}"


def plural(n, singular, plural_form):
    """1 -> 'vínculo', 2 -> 'vínculos'. Evita 'São 1 vínculos em 1 NCMs'."""
    return singular if abs(n) == 1 else plural_form


def milhar(n):
    """1234567 -> 1.234.567"""
    return f"{n:,}".replace(",", ".")


def por_extenso(iso):
    """2026-08-30 -> 30 de agosto de 2026"""
    if not iso:
        return ""
    a, m, d = iso.split("-")
    return f"{int(d)} de {MESES[int(m)]} de {a}"


def dias_ate(iso, referencia):
    return (date.fromisoformat(iso) - referencia).days


RE_DATA = re.compile(r"\d{4}-\d{2}-\d{2}")


def data_valida(texto):
    """True se texto é uma data AAAA-MM-DD real (2026-02-30 não é)."""
    if not isinstance(texto, str) or not RE_DATA.fullmatch(texto):
        return False
    try:
        date.fromisoformat(texto)
    except ValueError:
        return False
    return True


def data_rfc822(iso):
    """2026-08-22 -> 'Sat, 22 Aug 2026 00:00:00 GMT', o formato que o RSS exige."""
    return formatdate(
        datetime.strptime(iso, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp(),
        usegmt=True,
    )


def capitulo(ncm):
    """8415.10.90 -> 84. O capítulo é o eixo natural de índice da NCM."""
    so_digitos = re.sub(r"\D", "", ncm or "")
    return so_digitos[:2] or "00"


# caminho publicado -> hash do conteúdo. Alimenta o lastmod do sitemap.
PAGINAS = {}


def escrever(caminho_relativo, html, assinatura=None):
    """Grava a página e registra o hash do conteúdo que importa.

    A assinatura é calculada por quem chama, a partir dos DADOS da página
    (ver assinatura_dados), e não do HTML: senão TODA página mudaria de hash
    todo dia por causa do rodapé com a data da coleta, e o lastmod voltaria
    a ser a mentira que era.
    """
    destino = os.path.join(DIR_SITE, caminho_relativo)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(destino, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    caminho = "/" + caminho_relativo.replace("\\", "/").replace("index.html", "")
    if assinatura is not None:
        PAGINAS[caminho] = hashlib.sha256(assinatura.encode("utf-8")).hexdigest()[:12]
    return caminho


def assinatura_dados(titulo, descricao, dados):
    """Assinatura do lastmod por DADOS, não por HTML.

    A versão anterior hasheava o corpo da página inteira. Só que "Faltam N
    dias" e a barra de prazo estão no corpo: a home e toda página com virada
    mudavam de hash todo dia, o lastmod virava "hoje" para elas e o IndexNow
    recebia um ping diário sem que nada tivesse mudado de verdade. Aqui
    entra só o que determina o conteúdo (viradas com as datas, atributos,
    órgãos, domínio) - nunca a contagem de dias nem a data da coleta. Uma
    troca de marcação no template também não bate o lastmod das 11 mil
    páginas, o que é o comportamento honesto: o conteúdo não mudou.

    Título e descrição entram porque são conteúdo indexado - e nenhum dos
    dois carrega a contagem de dias (o h1 da home carrega, e fica de fora).
    """
    return titulo + descricao + json.dumps(dados, sort_keys=True, ensure_ascii=False)


def virada_estavel(v):
    """Projeção de uma virada sem nada que dependa do dia de hoje.

    É o que vai para a assinatura das páginas: o prazo em dias e a barra de
    urgência são derivados da data de referência e ficam fora.
    """
    return {
        "ncm": v.get("ncm"),
        "atributo": v.get("atributo"),
        "nome": v.get("nome"),
        "orgaos": v.get("orgaos") or [],
        "vira_obrigatorio_em": v.get("vira_obrigatorio_em"),
    }


def bloco_formulario(cfg):
    url_form = cfg.get("form_embed_url")
    if url_form:
        return (
            f'<iframe src="{esc(url_form)}" loading="lazy" '
            f'title="Cadastro para receber os avisos"></iframe>'
        )

    email = cfg.get("contato_email")
    if email:
        assunto = "Quero acompanhar minhas NCMs"
        # Sem escapes de nova linha aqui: montado por join para não quebrar
        # em nenhuma camada de shell ou heredoc.
        corpo = chr(10).join(
            [
                "Minhas NCMs (uma por linha):",
                "",
                "",
                "",
                "--",
                "Quantos SKUs voce mantem no catalogo?",
                "Como voce descobre hoje que um atributo vai virar obrigatorio?",
            ]
        )
        href = (
            f"mailto:{email}"
            f"?subject={urllib.parse.quote(assunto)}"
            f"&body={urllib.parse.quote(corpo)}"
        )
        return (
            f'<p><a class="botao" href="{esc(href)}">'
            f"Enviar minhas NCMs por e-mail</a></p>"
            f'<p style="font-size:.86rem;color:var(--faint);margin-top:12px">'
            f"Abre seu cliente de e-mail com a mensagem pronta. "
            f"Sem cadastro e sem senha.</p>"
        )

    return (
        '<p class="pendente">[captura ainda não configurada — '
        "definir contato_email ou form_embed_url em config.json]</p>"
    )


def bloco_analytics(cfg):
    codigo = cfg.get("goatcounter_code")
    if not codigo:
        return ""
    return (
        '<script data-goatcounter="'
        f'https://{esc(codigo)}.goatcounter.com/count"'
        ' async src="https://gc.zgo.at/count.js"></script>'
    )


def bloco_jsonld(dados):
    """JSON-LD com o < neutralizado.

    json.dumps não escapa <, então um "</script>" vindo do arquivo oficial
    fecharia a tag e o resto viraria HTML. Já existem 26 campos com "<" nos
    dados publicados hoje.
    """
    if not dados:
        return ""
    corpo = (
        json.dumps(dados, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    return f'<script type="application/ld+json">{corpo}</script>'


def trilha_dados(cfg, itens):
    """BreadcrumbList - o dado estruturado com retorno visível no resultado."""
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "name": nome,
                "item": absoluta(cfg, caminho),
            }
            for i, (nome, caminho) in enumerate(itens)
        ],
    }


def trilha_html(cfg, itens):
    """A mesma trilha, visível.

    O JSON-LD de breadcrumb existia sem nada na página. O Google descarta o
    rich result quando a marcação não corresponde a conteúdo visível - e o
    leitor ficava sem caminho de volta para o índice.
    """
    if not itens:
        return ""
    partes = []
    for i, (nome, caminho) in enumerate(itens):
        if i == len(itens) - 1:
            partes.append(f'<li aria-current="page">{esc(nome)}</li>')
        else:
            partes.append(f'<li><a href="{esc(url(cfg, caminho))}">{esc(nome)}</a></li>')
    return (
        '<nav class="trilha" aria-label="Trilha de navegação"><ol>'
        + "".join(partes)
        + "</ol></nav>"
    )


ESTATICOS = {}


def pagina(
    cfg, snapshot, corpo, titulo, descricao, caminho=None, itens_trilha=None, jsonld=None
):
    """Monta a página completa sobre base.html.

    caminho=None é a página de erro: ela não tem URL própria (o Pages a serve
    em qualquer endereço ausente), então sai sem canonical e sem og:url, e
    com noindex - um canonical fixo em /404/ convidaria o Google a indexar
    a página de erro como se fosse conteúdo.
    """
    trilha = trilha_html(cfg, itens_trilha or [])
    estruturado = jsonld
    if estruturado is None and itens_trilha:
        estruturado = trilha_dados(cfg, itens_trilha)
    if caminho is None:
        canonicos = ""
        meta_extra = '<meta name="robots" content="noindex">'
    else:
        canonical = esc(absoluta(cfg, caminho) or caminho)
        canonicos = (
            f'<link rel="canonical" href="{canonical}">\n'
            f'<meta property="og:url" content="{canonical}">'
        )
        meta_extra = ""
    return preencher(
        template("base.html"),
        {
            "titulo": esc(titulo),
            "descricao": esc(descricao),
            "canonicos": canonicos,
            "meta_extra": meta_extra,
            "conteudo": corpo,
            "trilha": trilha,
            "base": esc(prefixo(cfg)),
            "css": esc(url(cfg, ESTATICOS.get("css", "/estilo.css"))),
            "js": esc(url(cfg, ESTATICOS.get("js", "/app.js"))),
            "og_imagem": esc(absoluta(cfg, "/og.png")),
            "coletado_em": br(snapshot["data_referencia"]),
            "versao": esc(snapshot["contagens"]["versao"]),
            "formulario": bloco_formulario(cfg),
            "analytics": bloco_analytics(cfg) + bloco_jsonld(estruturado),
        },
    )


# ---------------------------------------------------------------- fragmentos


def rotulo(texto):
    """data-rot: abaixo de 640px a tabela vira cartao e cada celula precisa
    anunciar o proprio rotulo, porque o thead fica escondido."""
    return f' data-rot="{esc(texto)}"'


def tabela_viradas(cfg, viradas, referencia):
    if not viradas:
        # Uma tabela com uma célula solta não é tabela: leitor de tela
        # anunciava "tabela, 1 linha, 1 coluna" para uma frase.
        return '<p class="pendente">Nenhuma virada agendada no arquivo de hoje.</p>'
    linhas = []
    for v in viradas:
        dias = dias_ate(v["vira_obrigatorio_em"], referencia)
        prazo = "hoje" if dias == 0 else ("amanhã" if dias == 1 else f"em {dias} dias")
        # A barra da a leitura visual do prazo: 30 dias enche, hoje quase vazia.
        largura = min(100, max(6, round(dias / 30 * 100)))
        # Urgência acende só abaixo de 7 dias - por isso significa algo.
        urg = " urgente" if dias <= 7 else ""
        linhas.append(
            f"<tr>"
            f'<td class="ncm"{rotulo("NCM")}>'
            f'<a href="{esc(url(cfg, "/ncm/" + v["ncm"] + "/"))}">{esc(v["ncm"])}</a></td>'
            f"<td{rotulo('Atributo')}>"
            f'<a href="{esc(url(cfg, "/atributos/" + v["atributo"] + "/"))}">'
            f"{esc(v['nome'] or v['atributo'])}</a>"
            f'<br><span class="cod-inline">{esc(v["atributo"])}</span></td>'
            f"<td{rotulo('Órgão')}>{esc('/'.join(v['orgaos']) or '—')}</td>"
            f'<td class="data"{rotulo("Vira obrigatório em")}>'
            f"{br(v['vira_obrigatorio_em'])}"
            f'<br><span class="prazo-txt{urg}">{prazo}</span>'
            f'<span class="prazo{urg}"><i style="--w:{largura}%"></i></span>'
            f"</td>"
            f"</tr>"
        )
    return (
        '<div class="rolagem" tabindex="0" role="region" '
        'aria-label="Viradas agendadas"><table>'
        "<caption>Atributos com data marcada para virar obrigatórios</caption>"
        '<thead><tr><th scope="col">NCM</th><th scope="col">Atributo</th>'
        '<th scope="col">Órgão</th>'
        '<th scope="col" class="data">Vira obrigatório em</th></tr></thead>'
        f"<tbody>{''.join(linhas)}</tbody></table></div>"
    )


def _celula_atributo(cfg, a, com_pagina, detalhes):
    """Nome do atributo: link quando existe página, texto quando não existe.

    Quem não tem página própria mostra aqui a orientação oficial e as opções
    válidas. O conteúdo não sumiu com o corte das páginas quase-duplicadas -
    ele passou a viver na página da NCM, que é onde ele sempre pertenceu.
    """
    codigo = a["codigo"]
    nome = a.get("nome") or codigo
    if codigo in com_pagina:
        alvo = esc(url(cfg, f"/atributos/{codigo}/"))
        return f'<a href="{alvo}">{esc(nome)}</a>'

    partes = [esc(nome)]
    d = detalhes.get(codigo) or {}
    if d.get("t"):
        partes.append(f'<p class="orienta">{esc(d["t"])}</p>')
    opcoes = d.get("d") or []
    if opcoes:
        itens = "".join(f"<dt>{esc(c)}</dt><dd>{esc(desc)}</dd>" for c, desc in opcoes)
        total = d.get("dt", len(opcoes))
        resto = ""
        if total > len(opcoes):
            resto = (
                f'<dd style="grid-column:1/-1;color:var(--faint)">'
                f"e mais {milhar(total - len(opcoes))} opções no "
                f"arquivo oficial</dd>"
            )
        partes.append(
            f'<details class="opcoes"><summary>ver as {milhar(total)} '
            f"{plural(total, 'opção válida', 'opções válidas')}</summary>"
            f"<dl>{itens}{resto}</dl></details>"
        )
    return "".join(partes)


def tabela_atributos_ncm(cfg, atributos, com_pagina, detalhes):
    linhas = []
    for a in atributos:
        if a.get("vira_obrigatorio_em"):
            marca = (
                f'<span class="tag muda">vira obrigatório em '
                f"{br(a['vira_obrigatorio_em'])}</span>"
            )
        elif a.get("obrigatorio"):
            marca = '<span class="tag obr">obrigatório</span>'
        else:
            marca = '<span class="tag opc">opcional</span>'
        codigo = a["codigo"]
        if codigo in com_pagina:
            cod_html = (
                f'<a href="{esc(url(cfg, "/atributos/" + codigo + "/"))}">{esc(codigo)}</a>'
            )
        else:
            cod_html = f'<span class="sem-pagina">{esc(codigo)}</span>'
        linhas.append(
            f"<tr>"
            f'<td class="cod"{rotulo("Código")}>{cod_html}</td>'
            f"<td{rotulo('Atributo')}>"
            f"{_celula_atributo(cfg, a, com_pagina, detalhes)}</td>"
            f"<td{rotulo('Órgão')}>{esc('/'.join(a.get('orgaos') or []) or '—')}</td>"
            f"<td{rotulo('Modalidade')}>{esc(a.get('modalidade') or '—')}</td>"
            f"<td{rotulo('Situação')}>{marca}</td>"
            f"</tr>"
        )
    return (
        '<div class="rolagem" tabindex="0" role="region" '
        'aria-label="Atributos desta NCM"><table>'
        "<caption>Atributos exigidos para esta NCM</caption>"
        '<thead><tr><th scope="col">Código</th><th scope="col">Atributo</th>'
        '<th scope="col">Órgão</th><th scope="col">Modalidade</th>'
        '<th scope="col">Situação</th></tr></thead>'
        f"<tbody>{''.join(linhas)}</tbody></table></div>"
    )


def _snapshot_historico(caminho):
    """Lê um snapshot antigo sem confiar no formato.

    bloco_historico lê até 30 arquivos escritos por até 30 versões do código.
    O formato já mudou uma vez (atributos_destaque saiu no segundo dia) e
    sobreviveu por sorte. Arquivo ilegível ou de formato desconhecido é
    ignorado, não derruba o build.
    """
    try:
        with open(caminho, encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(s, dict) or not isinstance(s.get("viradas"), list):
        return None
    # Os primeiros snapshots não carregavam "schema": são o formato 1.
    schema = s.get("schema")
    if schema is not None and not (isinstance(schema, int) and schema <= SCHEMA_SUPORTADO):
        print(
            f"AVISO: {caminho} tem schema {schema!r}, acima do suportado "
            f"({SCHEMA_SUPORTADO}); ignorado.",
            file=sys.stderr,
        )
        return None
    return s


def arquivos_historico(referencia, janela=None):
    """Os snapshots diários até a data de referência: [(data, caminho)].

    Com janela, só os dos últimos N dias - e são DIAS, não arquivos: um dia
    perdido por falha de rede alargava silenciosamente o período. Nome que
    não é data (um .json perdido na pasta) é ignorado.
    """
    if not os.path.isdir(DIR_HISTORICO):
        return []
    limite = referencia - janela if janela else None
    arquivos = []
    for nome in sorted(os.listdir(DIR_HISTORICO)):
        if not nome.endswith(".json"):
            continue
        try:
            quando = date.fromisoformat(nome[:-5])
        except ValueError:
            continue
        if quando > referencia or (limite and quando < limite):
            continue
        arquivos.append((quando, os.path.join(DIR_HISTORICO, nome)))
    return arquivos


def primeira_vista(referencia):
    """Primeira data em que cada virada apareceu no histórico.

    Devolve {(ncm, atributo, vira_obrigatorio_em): "AAAA-MM-DD"}. É o pubDate
    honesto do feed: antes todo item levava a data da coleta do dia, e um
    leitor de feed via 14 itens "novos" toda manhã. A chave inclui a data da
    virada porque um adiamento é, para quem acompanha, uma notícia nova.

    Varre o histórico inteiro, não só a janela de 30 dias: com a janela, o
    pubDate de uma virada antiga andaria um dia para a frente a cada build.
    """
    vista = {}
    for quando, caminho in arquivos_historico(referencia):
        s = _snapshot_historico(caminho)
        if not s:
            continue
        for v in s["viradas"]:
            chave = (v.get("ncm"), v.get("atributo"), v.get("vira_obrigatorio_em"))
            if all(chave) and chave not in vista:
                vista[chave] = quando.isoformat()
    return vista


def bloco_historico(cfg, referencia, ncms_com_pagina=frozenset()):
    """O que mudou nos ultimos 30 dias, montado do arquivo diario.

    O endpoint oficial ignora ?data= e não serve versões passadas: sem este
    arquivo local não existe 'o que mudou'. E também o que impede a página de
    ficar vazia entre um lote de viradas e o próximo. As NCMs em
    ncms_com_pagina viram link; as demais (uma NCM que saiu do Catálogo)
    ficam como texto, para o site continuar fechado.
    """
    arquivos = arquivos_historico(referencia, JANELA_HISTORICO)
    if len(arquivos) < 2:
        return ""

    atual = _snapshot_historico(arquivos[-1][1])
    if not atual:
        return ""
    vistos_antes = {}
    for _, caminho in arquivos[:-1]:
        s = _snapshot_historico(caminho)
        if not s:
            continue
        for v in s["viradas"]:
            if v.get("ncm") and v.get("atributo"):
                vistos_antes[(v["ncm"], v["atributo"])] = v.get("nome")

    agora = {
        (v["ncm"], v["atributo"]): v
        for v in atual["viradas"]
        if v.get("ncm") and v.get("atributo")
    }
    novas = [v for k, v in agora.items() if k not in vistos_antes]
    sumiram = sorted(k for k in vistos_antes if k not in agora)

    if not novas and not sumiram:
        return ""

    def item(ncm, texto):
        if ncm in ncms_com_pagina:
            alvo = esc(url(cfg, f"/ncm/{ncm}/"))
            return f'<li><a href="{alvo}">{esc(ncm)}</a> — {esc(texto)}</li>'
        return f"<li>{esc(ncm)} — {esc(texto)}</li>"

    partes = ["<h2>O que mudou nos últimos 30 dias</h2>"]
    if novas:
        itens = "".join(
            item(
                v["ncm"],
                f"{v.get('nome') or v['atributo']}, a partir de "
                f"{br(v['vira_obrigatorio_em'])}",
            )
            for v in sorted(novas, key=lambda x: x["vira_obrigatorio_em"])
        )
        partes.append(f"<h3>Viradas novas</h3><ul>{itens}</ul>")
    if sumiram:
        # Mostra o NOME, como a lista de cima. Antes esta mostrava o código
        # cru (ATT_13241) para o mesmo conceito.
        itens = "".join(item(n, vistos_antes.get((n, c)) or c) for n, c in sumiram)
        partes.append(
            "<h3>Saíram da lista</h3><p style='font-size:.92rem;color:var(--muted)'>"
            "Já passaram da data ou foram removidas pela Receita.</p>"
            f"<ul>{itens}</ul>"
        )
    return "".join(partes)


# ---------------------------------------------------------------- páginas


def gerar_index(cfg, s, ncms_com_pagina=frozenset()):
    ref = date.fromisoformat(s["data_referencia"])
    vs = s["viradas"]
    caminhos = []

    if vs:
        proxima = vs[0]["vira_obrigatorio_em"]
        dias = dias_ate(proxima, ref)
        ncms = len({v["ncm"] for v in vs})
        orgaos_lista = sorted({o for v in vs for o in v["orgaos"]})
        datas = sorted({v["vira_obrigatorio_em"] for v in vs})
        no_corte = sum(1 for v in vs if v["vira_obrigatorio_em"] == proxima)
        seguinte = br(datas[1]) if len(datas) > 1 else "—"
        unidade = plural(dias, "dia", "dias")
        juntos = esc("/".join(orgaos_lista))
        # O número grande e o "dias" são visuais (aria-hidden, porque o JS
        # conta de 0 até N e o leitor de tela ouviria a contagem inteira).
        # A frase completa vai num span só para leitor de tela, como
        # conteúdo de verdade: aria-label em <p> é ignorado pela maioria
        # dos leitores, que não tratam parágrafo como elemento nomeável.
        if dias == 0:
            frase = f"O próximo corte é hoje, {br(proxima)}."
        else:
            frase = (
                f"{'Falta' if dias == 1 else 'Faltam'} {dias} {unidade} "
                f"para o próximo corte, em {br(proxima)}."
            )
        cartao = (
            '<div class="contagem-cartao">'
            '<p class="contagem-topo">'
            f'<span class="oculto">{esc(frase)}</span>'
            f'<span class="contagem-num" data-contagem="{dias}" aria-hidden="true"'
            f' style="--digitos:{len(str(dias))}">{dias}</span>'
            f'<span class="contagem-un" aria-hidden="true">{unidade}</span>'
            "</p>"
            '<div class="contagem-fatos">'
            f"<div><span>corte</span>{br(proxima)}</div>"
            f"<div><span>órgão</span>{juntos}</div>"
            f"<div><span>vínculos</span>{no_corte} de {len(vs)}</div>"
            f"<div><span>próximo</span>{seguinte}</div>"
            "</div></div>"
        )
        prazo_h1 = (
            "hoje"
            if dias == 0
            else ("amanhã" if dias == 1 else f"nos próximos {dias} dias")
        )
        h1 = (
            f"{len(vs)} {plural(len(vs), 'atributo', 'atributos')} de NCM "
            f"{plural(len(vs), 'vira', 'viram')} "
            f"{plural(len(vs), 'obrigatório', 'obrigatórios')} {prazo_h1}"
        )
        lede = (
            f"{plural(len(vs), 'É', 'São')} {len(vs)} "
            f"{plural(len(vs), 'vínculo', 'vínculos')} em {ncms} "
            f"{plural(ncms, 'NCM', 'NCMs')}. "
            f"Os produtos {plural(ncms, 'dessa NCM', 'dessas NCMs')} que estiverem "
            f"sem "
            f"{plural(len(vs), 'o atributo preenchido', 'os atributos preenchidos')} "
            f"na data são desativados no Catálogo de Produtos do Portal Único."
        )
        descricao = (
            f"{len(vs)} {plural(len(vs), 'atributo', 'atributos')} em "
            f"{ncms} {plural(ncms, 'NCM', 'NCMs')} "
            f"{plural(len(vs), 'vira', 'viram')} "
            f"{plural(len(vs), 'obrigatório', 'obrigatórios')} no Catálogo de "
            f"Produtos do Portal Único. Próximo corte em {br(proxima)}."
        )
        cobertura = f"{s['data_referencia']}/{datas[-1]}"
    else:
        h1 = "Nenhum atributo de NCM tem virada agendada hoje"
        lede = (
            "O arquivo oficial de hoje não traz nenhum vínculo com data para virar "
            "obrigatório. Esta página é atualizada todo dia — quando a Receita "
            "agendar uma nova virada, ela aparece aqui."
        )
        cartao = (
            '<div class="contagem-cartao">'
            '<div class="contagem-fatos" style="margin:0;padding:0;border:0">'
            "<div><span>vínculos com data</span>0</div>"
            "<div><span>releitura</span>amanhã 09:00 UTC</div>"
            "</div></div>"
        )
        descricao = (
            "Monitoramento diário dos atributos de NCM que viram obrigatórios "
            "no Catálogo de Produtos do Portal Único."
        )
        cobertura = s["data_referencia"]

    historico = bloco_historico(cfg, ref, ncms_com_pagina)
    corpo = preencher(
        template("index.html"),
        {
            "data_ref": br(s["data_referencia"]),
            "h1": esc(h1),
            "lede": esc(lede),
            "cartao": cartao,
            "tabela": tabela_viradas(cfg, vs, ref),
            "historico": historico,
            "base": esc(prefixo(cfg)),
        },
    )
    titulo = (
        "Atributos de NCM que viram obrigatórios — Catálogo do Portal Único"
        if vs
        else "Atributos de NCM com virada agendada — Portal Único"
    )
    dataset = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "Atributos por NCM com virada agendada — Catálogo de Produtos",
        "description": (
            "Extração diária da relação oficial de atributos por NCM do "
            "Portal Único, isolando os que têm data marcada para virar "
            "obrigatórios."
        ),
        "url": absoluta(cfg, "/"),
        "inLanguage": "pt-BR",
        "isAccessibleForFree": True,
        "license": "https://creativecommons.org/publicdomain/zero/1.0/",
        "creativeWorkStatus": "Published",
        "dateModified": s["data_referencia"],
        "isBasedOn": s.get("fonte"),
        "keywords": [
            "NCM",
            "Portal Único",
            "Catálogo de Produtos",
            "DUIMP",
            "Siscomex",
            "atributos",
            "comércio exterior",
        ],
        "spatialCoverage": {"@type": "Country", "name": "Brasil"},
        "creator": {
            "@type": "GovernmentOrganization",
            "name": "Portal Único de Comércio Exterior — Receita Federal",
        },
        "temporalCoverage": cobertura,
        "distribution": [
            {
                "@type": "DataDownload",
                "encodingFormat": "application/rss+xml",
                "contentUrl": absoluta(cfg, "/feed.xml"),
            }
        ],
    }
    # O h1 ("nos próximos N dias") e o cartão ficam fora da assinatura de
    # propósito; o bloco "o que mudou" entra porque a janela de 30 dias
    # deslizando é mudança de conteúdo de verdade.
    caminhos.append(
        escrever(
            "index.html",
            pagina(cfg, s, corpo, titulo, descricao, "/", jsonld=dataset),
            assinatura=assinatura_dados(
                titulo,
                descricao,
                {
                    "viradas": [virada_estavel(v) for v in vs],
                    "historico": historico,
                },
            ),
        )
    )
    return caminhos


def gerar_ncms(cfg, s, completo, com_pagina):
    """Uma página por NCM do Catálogo, não só pelas que têm virada.

    Antes eram 9 páginas de NCM, de 10.571 existentes. A pergunta que o
    público faz é "a MINHA NCM exige o quê?" - e ninguém pesquisa por
    ATT_13240. Este é o eixo que responde.
    """
    ref = date.fromisoformat(s["data_referencia"])
    por_ncm = {}
    for v in s["viradas"]:
        por_ncm.setdefault(v["ncm"], []).append(v)
    fichas = {f["ncm"]: f for f in s.get("ncms_afetadas", [])}
    detalhes = completo.get("atributos", {})
    caminhos = []

    for ncm in sorted(completo.get("ncms", {})):
        vs = sorted(por_ncm.get(ncm, []), key=lambda x: x["vira_obrigatorio_em"])
        cap = capitulo(ncm)
        itens_trilha = [
            ("Início", "/"),
            ("NCMs", "/ncm/"),
            (f"Capítulo {cap}", f"/ncm/capitulo-{cap}/"),
            (f"NCM {ncm}", f"/ncm/{ncm}/"),
        ]

        if ncm in fichas:
            atributos = fichas[ncm]["atributos"]
        else:
            atributos = []
            for codigo, obrigatorio, modalidade in completo["ncms"][ncm]:
                d = detalhes.get(codigo) or {}
                atributos.append(
                    {
                        "codigo": codigo,
                        "nome": d.get("n"),
                        "obrigatorio": obrigatorio,
                        "modalidade": modalidade,
                        "orgaos": d.get("o") or [],
                        "vira_obrigatorio_em": None,
                    }
                )
            atributos.sort(key=lambda a: (not a["obrigatorio"], a["codigo"]))

        orgaos_lista = sorted({o for a in atributos for o in (a.get("orgaos") or [])})
        obrigatorios = sum(1 for a in atributos if a.get("obrigatorio"))

        if vs:
            proxima = vs[0]["vira_obrigatorio_em"]
            dias = dias_ate(proxima, ref)
            nomes = sorted({v["nome"] or v["atributo"] for v in vs})
            orgaos_v = sorted({o for v in vs for o in v["orgaos"]})
            h1 = (
                f"NCM {ncm}: {len(vs)} {plural(len(vs), 'atributo', 'atributos')} "
                f"{plural(len(vs), 'vira', 'viram')} obrigatório em {br(proxima)}"
            )
            preenchidos = plural(
                len(vs), "esse atributo preenchido", "esses atributos preenchidos"
            )
            lede = (
                f"Produtos classificados na NCM {ncm} sem {preenchidos} "
                f"serão desativados no Catálogo de Produtos "
                f"a partir de {por_extenso(proxima)}."
            )
            itens = "".join(f"<li><strong>{esc(n)}</strong></li>" for n in nomes)
            urgencia = (
                "É hoje."
                if dias == 0
                else ("Falta 1 dia." if dias == 1 else f"Faltam {dias} dias.")
            )
            # O nome do órgão vem do arquivo oficial: era o único dado
            # interpolado sem esc() em todo o gerador.
            exigencia = (
                f"do {esc(orgaos_v[0])}" if len(orgaos_v) == 1 else "dos órgãos anuentes"
            )
            aviso = (
                f'<div class="aviso"><strong>{urgencia}</strong> '
                f"Exigência {exigencia}. "
                f'Atributos afetados:<ul style="margin:8px 0 0">{itens}</ul></div>'
            )
            titulo = f"NCM {ncm} — atributos que viram obrigatórios em {br(proxima)}"
            descricao = (
                f"NCM {ncm}: {len(vs)} "
                f"{plural(len(vs), 'atributo', 'atributos')} do Catálogo de "
                f"Produtos do Portal Único {plural(len(vs), 'vira', 'viram')} "
                f"{plural(len(vs), 'obrigatório', 'obrigatórios')} em "
                f"{br(proxima)}. Lista completa dos atributos exigidos para "
                f"esta NCM."
            )
            corpo = preencher(
                template("ncm.html"),
                {
                    "ncm": esc(ncm),
                    "h1": esc(h1),
                    "lede": esc(lede),
                    "aviso": aviso,
                    "tabela": tabela_atributos_ncm(cfg, atributos, com_pagina, detalhes),
                },
            )
        else:
            h1 = (
                f"NCM {ncm}: {len(atributos)} "
                f"{plural(len(atributos), 'atributo exigido', 'atributos exigidos')} "
                f"no Catálogo de Produtos"
            )
            lede = (
                f"Para cadastrar um produto na NCM {ncm} no Catálogo de Produtos do "
                f"Portal Único são {len(atributos)} "
                f"{plural(len(atributos), 'atributo', 'atributos')}, "
                f"{obrigatorios} {plural(obrigatorios, 'obrigatório', 'obrigatórios')}"
                f"{', exigidos por ' + '/'.join(orgaos_lista) if orgaos_lista else ''}"
                f". "
                f"Nenhum tem virada agendada hoje."
            )
            aviso = ""
            titulo = f"NCM {ncm} — atributos exigidos no Catálogo de Produtos"
            descricao = (
                f"Os {len(atributos)} atributos exigidos para a NCM {ncm} no "
                f"Catálogo de Produtos do Portal Único, com órgão anuente, "
                f"opções válidas e situação de cada um."
            )
            corpo = preencher(
                template("ncm_simples.html"),
                {
                    "ncm": esc(ncm),
                    "h1": esc(h1),
                    "lede": esc(lede),
                    "aviso": aviso,
                    "tabela": tabela_atributos_ncm(cfg, atributos, com_pagina, detalhes),
                },
            )

        # A tabela mostra, para quem não tem página própria, a orientação e
        # as opções do atributo: isso é conteúdo da página e entra aqui.
        dados_pagina = {
            "ncm": ncm,
            "viradas": [virada_estavel(v) for v in vs],
            "atributos": [
                {
                    "codigo": a["codigo"],
                    "nome": a.get("nome"),
                    "obrigatorio": bool(a.get("obrigatorio")),
                    "modalidade": a.get("modalidade"),
                    "orgaos": a.get("orgaos") or [],
                    "vira_obrigatorio_em": a.get("vira_obrigatorio_em"),
                    "pagina": a["codigo"] in com_pagina,
                    "detalhe": (
                        None if a["codigo"] in com_pagina else detalhes.get(a["codigo"])
                    ),
                }
                for a in atributos
            ],
        }
        caminhos.append(
            escrever(
                f"ncm/{ncm}/index.html",
                pagina(cfg, s, corpo, titulo, descricao, f"/ncm/{ncm}/", itens_trilha),
                assinatura=assinatura_dados(titulo, descricao, dados_pagina),
            )
        )

    caminhos += gerar_capitulos(cfg, s, completo, por_ncm)
    return caminhos


def gerar_capitulos(cfg, s, completo, por_ncm):
    """Índice por capítulo NCM: o caminho de rastreio até as 10 mil páginas."""
    caminhos = []
    por_capitulo = {}
    for ncm in sorted(completo.get("ncms", {})):
        por_capitulo.setdefault(capitulo(ncm), []).append(ncm)

    for cap, ncms in sorted(por_capitulo.items()):
        blocos = [ncms[i : i + POR_PAGINA] for i in range(0, len(ncms), POR_PAGINA)] or [[]]
        for numero, bloco in enumerate(blocos, start=1):
            sufixo = "" if numero == 1 else f"pagina-{numero}/"
            caminho = f"/ncm/capitulo-{cap}/{sufixo}"
            itens = "".join(
                f'<li><a href="{esc(url(cfg, "/ncm/" + n + "/"))}">{esc(n)}</a>'
                + (' <span class="tag muda">virada</span>' if n in por_ncm else "")
                + "</li>"
                for n in bloco
            )
            nav = ""
            if len(blocos) > 1:
                partes = []
                for i in range(1, len(blocos) + 1):
                    destino = "" if i == 1 else f"pagina-{i}/"
                    alvo = esc(url(cfg, f"/ncm/capitulo-{cap}/{destino}"))
                    atual = ' aria-current="page"' if i == numero else ""
                    partes.append(f'<a href="{alvo}"{atual}>{i}</a>')
                nav = (
                    '<nav class="paginacao" aria-label="Páginas do capítulo">'
                    f"{''.join(partes)}</nav>"
                )
            com_virada = sum(1 for n in bloco if n in por_ncm)
            corpo = (
                f'<span class="chapeu">Capítulo {esc(cap)}</span>'
                f"<h1>NCMs do capítulo {esc(cap)} no Catálogo de Produtos</h1>"
                f'<p class="lede">{len(ncms)} '
                f"{plural(len(ncms), 'NCM do capítulo', 'NCMs do capítulo')} "
                f"{esc(cap)} têm atributo exigido no Catálogo de Produtos do "
                f"Portal Único"
                + (
                    f", e {com_virada} {plural(com_virada, 'tem', 'têm')} virada agendada."
                    if com_virada
                    else "."
                )
                + f'</p><ul class="limpa">{itens}</ul>{nav}'
            )
            titulo = f"Capítulo {cap} — NCMs e atributos do Catálogo de Produtos" + (
                f" (página {numero})" if numero > 1 else ""
            )
            # A faixa de NCMs e o número da página diferenciam as descriptions
            # das páginas de um mesmo capítulo, que antes saíam idênticas.
            faixa = (
                f" ({bloco[0]} a {bloco[-1]})"
                if len(bloco) > 1
                else (f" ({bloco[0]})" if bloco else "")
            )
            descricao = (
                f"As NCMs do capítulo {cap}{faixa} com atributos exigidos no "
                f"Catálogo de Produtos do Portal Único"
                + (f", página {numero} de {len(blocos)}." if len(blocos) > 1 else ".")
            )
            caminhos.append(
                escrever(
                    f"ncm/capitulo-{cap}/{sufixo}index.html",
                    pagina(
                        cfg,
                        s,
                        corpo,
                        titulo,
                        descricao,
                        caminho,
                        [("Início", "/"), ("NCMs", "/ncm/"), (f"Capítulo {cap}", caminho)],
                    ),
                    assinatura=assinatura_dados(
                        titulo,
                        descricao,
                        {
                            "capitulo": cap,
                            "ncms": bloco,
                            "total": len(ncms),
                            "paginas": len(blocos),
                            "numero": numero,
                            "com_virada": [n for n in bloco if n in por_ncm],
                        },
                    ),
                )
            )

    itens = "".join(
        f'<li><a href="{esc(url(cfg, "/ncm/capitulo-" + c + "/"))}">{esc(c)} · '
        f"{len(ns)}</a></li>"
        for c, ns in sorted(por_capitulo.items())
    )
    total = sum(len(v) for v in por_capitulo.values())
    corpo = (
        f'<span class="chapeu">Índice</span>'
        f"<h1>NCMs do Catálogo de Produtos, por capítulo</h1>"
        f'<p class="lede">As {milhar(total)} NCMs que têm atributo exigido no '
        f"Catálogo de Produtos do Portal Único, agrupadas pelos "
        f"{len(por_capitulo)} capítulos da nomenclatura. Cada página lista os "
        f"atributos exigidos, o órgão que exige e as opções válidas.</p>"
        f'<ul class="limpa">{itens}</ul>'
    )
    titulo = "NCMs do Catálogo de Produtos do Portal Único — índice por capítulo"
    descricao = (
        "Índice de todas as NCMs com atributos exigidos no Catálogo de "
        "Produtos do Portal Único, por capítulo da nomenclatura."
    )
    caminhos.append(
        escrever(
            "ncm/index.html",
            pagina(
                cfg,
                s,
                corpo,
                titulo,
                descricao,
                "/ncm/",
                [("Início", "/"), ("NCMs", "/ncm/")],
            ),
            assinatura=assinatura_dados(
                titulo,
                descricao,
                {
                    "capitulos": {c: len(ns) for c, ns in por_capitulo.items()},
                },
            ),
        )
    )
    return caminhos


def titulo_atributo(a):
    """Título discriminante.

    O molde anterior era "<nome> (<codigo>) — Catálogo de Produtos do Portal
    Único", e as 888 páginas produziam 118 títulos distintos: 586 eram
    "Destaque (ATT_N)". O órgão e a NCM já estavam em memória.
    """
    nome = a.get("nome") or a["codigo"]
    orgaos_lista = a.get("orgaos") or []
    partes = [f"{nome} ({a['codigo']})"]
    if orgaos_lista:
        partes.append("atributo " + "/".join(orgaos_lista))
    if a["total_ncms"] <= 3 and a.get("ncms"):
        partes.append("NCM " + ", ".join(a["ncms"]))
    return " — ".join(partes) + " — Portal Único"


def gerar_atributos(cfg, s, catalogo, com_pagina, ncms_com_pagina=frozenset()):
    """Uma página por atributo do catálogo (os que merecem página).

    ncms_com_pagina decide se uma NCM citada vira link ou texto: o site só
    fecha porque nenhum link é emitido sem a página correspondente.
    """
    caminhos = []
    virando = {}
    for v in s["viradas"]:
        virando.setdefault(v["atributo"], []).append(v)
    slug_por_orgao = {o["orgao"]: o["slug"] for o in catalogo["orgaos"]}

    def chip_ncm(n):
        if n in ncms_com_pagina:
            return f'<li><a href="{esc(url(cfg, "/ncm/" + n + "/"))}">{esc(n)}</a></li>'
        return f'<li><span class="sem-pagina">{esc(n)}</span></li>'

    # Vizinhança: quem divide NCM com quem. Transforma a topologia em estrela
    # (um único link de entrada, vindo da página do órgão) numa malha, e
    # responde a pergunta seguinte: "e o que mais eu preencho para essa NCM?"
    por_ncm = {}
    for a in catalogo["atributos"]:
        for n in a.get("ncms") or []:
            por_ncm.setdefault(n, []).append(a["codigo"])
    nomes = {a["codigo"]: a.get("nome") or a["codigo"] for a in catalogo["atributos"]}

    for a in catalogo["atributos"]:
        cod = a["codigo"]
        nome = a.get("nome") or cod
        vs = virando.get(cod, [])

        if vs:
            data = min(v["vira_obrigatorio_em"] for v in vs)
            ncms_v = sorted({v["ncm"] for v in vs})
            lista = "".join(chip_ncm(n) for n in ncms_v)
            aviso = (
                f'<div class="aviso"><strong>Este atributo vira obrigatório em '
                f"{br(data)}</strong> para {len(ncms_v)} "
                f"{plural(len(ncms_v), 'NCM', 'NCMs')}:"
                f'<ul class="limpa" style="margin-top:10px">{lista}</ul></div>'
            )
            h1 = f"{nome} ({cod}): vira obrigatório em {br(data)}"
            descricao = (
                f"{nome} ({cod}) no Catálogo de Produtos do Portal Único: "
                f"o que preencher, opções válidas e em quais NCMs se aplica. "
                f"Vira obrigatório em {br(data)}."
            )
        else:
            aviso = ""
            h1 = f"{nome} ({cod}): o que preencher no Catálogo de Produtos"
            onde = (
                f" Aplica-se a {milhar(a['total_ncms'])} "
                f"{plural(a['total_ncms'], 'NCM', 'NCMs')}."
            )
            descricao = (
                f"{nome} ({cod}) no Catálogo de Produtos do Portal Único: "
                f"o que preencher e opções válidas." + onde
            )

        lista_orgaos = a.get("orgaos") or []
        orgaos_txt = "/".join(lista_orgaos) or "—"
        forma = FORMA_PREENCHIMENTO.get(
            a.get("forma_preenchimento"), a.get("forma_preenchimento") or "—"
        )
        lede = esc(
            f"Atributo exigido por {orgaos_txt}, preenchido como {forma}. "
            f"Aplica-se a {milhar(a['total_ncms'])} "
            f"{plural(a['total_ncms'], 'NCM', 'NCMs')}."
        )

        definicao = (
            f"<h2>O que é</h2><p>{esc(a['definicao'])}</p>" if a.get("definicao") else ""
        )
        orientacao = (
            f"<h2>Orientação oficial de preenchimento</h2><p>{esc(a['orientacao'])}</p>"
            if a.get("orientacao")
            else ""
        )

        if a.get("dominio"):
            opcoes = "".join(
                f"<dt>{esc(d['codigo'])}</dt><dd>{esc(d['descricao'])}</dd>"
                for d in a["dominio"]
            )
            total_dom = a.get("dominio_total", len(a["dominio"]))
            corte = ""
            if total_dom > len(a["dominio"]):
                corte = (
                    f'<p style="font-size:.9rem;color:var(--faint)">Mostrando as '
                    f"{milhar(len(a['dominio']))} primeiras de "
                    f"{milhar(total_dom)} opções. A lista completa está no "
                    f"arquivo oficial.</p>"
                )
            dominio = (
                f"<h2>Opções válidas ({milhar(total_dom)})</h2>"
                f'<dl class="dominio">{opcoes}</dl>{corte}'
            )
        else:
            dominio = ""

        mostradas = a.get("ncms") or []
        if a["total_ncms"] > len(mostradas):
            aplicacao = (
                f"Este atributo está vinculado a {milhar(a['total_ncms'])} "
                f"NCMs. As {len(mostradas)} primeiras:"
            )
        else:
            aplicacao = (
                f"Este atributo está vinculado a "
                f"{milhar(a['total_ncms'])} "
                f"{plural(a['total_ncms'], 'NCM', 'NCMs')}:"
            )
        chips = "".join(chip_ncm(n) for n in mostradas)

        if lista_orgaos:
            links = " · ".join(
                f'<a href="{esc(url(cfg, "/orgaos/" + slug_por_orgao[o] + "/"))}">'
                f"{esc(o)}</a>"
                for o in lista_orgaos
                if o in slug_por_orgao
            )
            if links:
                lede += (
                    f' <span style="font-size:.92rem">Ver todos os atributos '
                    f"de {links}.</span>"
                )

        vizinhos = []
        for n in mostradas:
            for outro in por_ncm.get(n, []):
                if outro != cod and outro not in vizinhos:
                    vizinhos.append(outro)
            if len(vizinhos) >= 10:
                break
        relacionados = ""
        if vizinhos:
            itens = "".join(
                f'<li><a href="{esc(url(cfg, "/atributos/" + v + "/"))}">'
                f"{esc(nomes[v])}</a></li>"
                for v in vizinhos[:10]
            )
            relacionados = (
                f"<h2>Atributos que costumam vir junto</h2>"
                f"<p>Aparecem nas mesmas NCMs que este.</p>"
                f'<ul class="limpa">{itens}</ul>'
            )

        corpo = preencher(
            template("atributo.html"),
            {
                "codigo": esc(cod),
                "h1": esc(h1),
                "lede": lede,
                "aviso": aviso,
                "definicao": definicao,
                "orientacao": orientacao,
                "dominio": dominio,
                "aplicacao": esc(aplicacao),
                "relacionados": relacionados,
                "ncms": f'<ul class="limpa">{chips}</ul>' if chips else "",
            },
        )
        titulo = titulo_atributo(a)
        caminhos.append(
            escrever(
                f"atributos/{cod}/index.html",
                pagina(
                    cfg,
                    s,
                    corpo,
                    titulo,
                    descricao,
                    f"/atributos/{cod}/",
                    [
                        ("Início", "/"),
                        ("Atributos", "/atributos/"),
                        (nome, f"/atributos/{cod}/"),
                    ],
                ),
                assinatura=assinatura_dados(
                    titulo,
                    descricao,
                    {
                        "atributo": a,
                        "viradas": [virada_estavel(v) for v in vs],
                        "vizinhos": [[v, nomes[v]] for v in vizinhos[:10]],
                        "orgaos_com_pagina": [
                            o for o in lista_orgaos if o in slug_por_orgao
                        ],
                        "ncms_com_pagina": [n for n in mostradas if n in ncms_com_pagina],
                    },
                ),
            )
        )

    orgs = "".join(
        f'<li><a href="{esc(url(cfg, "/orgaos/" + o["slug"] + "/"))}">'
        f"{esc(o['orgao'])} · {o['total_atributos']}</a></li>"
        for o in catalogo["orgaos"]
    )
    destaque = [a for a in catalogo["atributos"] if a.get("nas_viradas")]
    itens = "".join(
        f'<li><a href="{esc(url(cfg, "/atributos/" + a["codigo"] + "/"))}">'
        f"{esc(a['codigo'])} · {esc(a.get('nome') or '')}</a></li>"
        for a in destaque
    )
    corpo = (
        f'<span class="chapeu">Índice</span><h1>Atributos do Catálogo de Produtos</h1>'
        f'<p class="lede">São {milhar(len(catalogo["atributos"]))} atributos com '
        f"página própria: os que têm orientação oficial de preenchimento, opções "
        f"válidas ou alcance largo. Os demais aparecem dentro da página da NCM em "
        f"que se aplicam. Navegue pelo órgão que exige cada um.</p>"
        f"<h2>Por órgão anuente</h2>"
        f'<ul class="limpa">{orgs}</ul>'
        + (f'<h2>Com virada agendada</h2><ul class="limpa">{itens}</ul>' if itens else "")
    )
    titulo = "Atributos do Catálogo de Produtos — Portal Único"
    descricao = (
        "Índice dos atributos do Catálogo de Produtos do Portal Único, com "
        "opções válidas e NCMs onde se aplicam."
    )
    caminhos.append(
        escrever(
            "atributos/index.html",
            pagina(
                cfg,
                s,
                corpo,
                titulo,
                descricao,
                "/atributos/",
                [("Início", "/"), ("Atributos", "/atributos/")],
            ),
            assinatura=assinatura_dados(
                titulo,
                descricao,
                {
                    "total": len(catalogo["atributos"]),
                    "orgaos": [
                        [o["slug"], o["orgao"], o["total_atributos"]]
                        for o in catalogo["orgaos"]
                    ],
                    "destaque": [[a["codigo"], a.get("nome")] for a in destaque],
                },
            ),
        )
    )
    return caminhos


def gerar_orgaos(cfg, s, catalogo):
    """Uma página por órgão anuente.

    Cria um eixo de consulta novo ("atributos anvisa duimp") e resolve o
    problema de um indice único com mais de mil itens.
    """
    caminhos = []
    virando = {a["codigo"] for a in catalogo["atributos"] if a.get("nas_viradas")}

    for o in catalogo["orgaos"]:
        linhas = []
        for a in o["atributos"]:
            marca = (
                '<span class="tag muda">virada agendada</span>'
                if a["codigo"] in virando
                else ""
            )
            forma = FORMA_PREENCHIMENTO.get(
                a.get("forma_preenchimento"), a.get("forma_preenchimento") or "—"
            )
            alvo = esc(url(cfg, f"/atributos/{a['codigo']}/"))
            # Sem marca, a célula sai sem rótulo: no cartão mobile um
            # "SITUAÇÃO" seguido de nada era uma pergunta sem resposta.
            linhas.append(
                f'<tr><td class="cod"{rotulo("Código")}>'
                f'<a href="{alvo}">{esc(a["codigo"])}</a></td>'
                f"<td{rotulo('Atributo')}>{esc(a.get('nome') or '—')}</td>"
                f"<td{rotulo('Preenchimento')}>{esc(forma)}</td>"
                f'<td class="num"{rotulo("NCMs")}>{milhar(a.get("total_ncms", 0))}</td>'
                f"<td{rotulo('Situação') if marca else ''}>{marca}</td></tr>"
            )
        tabela = (
            '<div class="rolagem" tabindex="0" role="region" '
            f'aria-label="Atributos exigidos pelo {esc(o["orgao"])}"><table>'
            f"<caption>Atributos exigidos pelo {esc(o['orgao'])}</caption>"
            '<thead><tr><th scope="col">Código</th>'
            '<th scope="col">Atributo</th><th scope="col">Preenchimento</th>'
            '<th scope="col" class="num">NCMs</th>'
            '<th scope="col"><span class="oculto">Situação</span></th></tr></thead>'
            f"<tbody>{''.join(linhas)}</tbody></table></div>"
        )

        n = o["total_atributos"]
        com_virada = [a for a in o["atributos"] if a["codigo"] in virando]
        h1 = f"Atributos exigidos pelo {o['orgao']} no Catálogo de Produtos"
        lede = (
            f"O {o['orgao']} exige {milhar(n)} "
            f"{plural(n, 'atributo', 'atributos')} no Catálogo de Produtos do "
            f"Portal Único. Cada um com orientação oficial de preenchimento e "
            f"as NCMs em que se aplica."
        )
        aviso = ""
        if com_virada:
            itens = "".join(
                f'<li><a href="{esc(url(cfg, "/atributos/" + a["codigo"] + "/"))}">'
                f"{esc(a.get('nome') or a['codigo'])}</a></li>"
                for a in com_virada
            )
            quantos = plural(
                len(com_virada), "atributo deste órgão tem", "atributos deste órgão têm"
            )
            aviso = (
                f'<div class="aviso"><strong>{len(com_virada)} {quantos} '
                f"virada agendada:</strong>"
                f'<ul class="limpa" style="margin-top:10px">{itens}</ul></div>'
            )

        corpo = preencher(
            template("orgao.html"),
            {
                "h1": esc(h1),
                "lede": esc(lede),
                "aviso": aviso,
                "tabela": tabela,
            },
        )
        titulo = f"Atributos do {o['orgao']} — Catálogo de Produtos do Portal Único"
        descricao = (
            f"Os {milhar(n)} atributos exigidos pelo {o['orgao']} no Catálogo "
            f"de Produtos do Portal Único, com orientação de preenchimento "
            f"e NCMs."
        )
        caminhos.append(
            escrever(
                f"orgaos/{o['slug']}/index.html",
                pagina(
                    cfg,
                    s,
                    corpo,
                    titulo,
                    descricao,
                    f"/orgaos/{o['slug']}/",
                    [
                        ("Início", "/"),
                        ("Órgãos", "/orgaos/"),
                        (o["orgao"], f"/orgaos/{o['slug']}/"),
                    ],
                ),
                assinatura=assinatura_dados(
                    titulo,
                    descricao,
                    {
                        "orgao": o,
                        "virando": sorted(a["codigo"] for a in com_virada),
                    },
                ),
            )
        )

    itens = "".join(
        f'<li><a href="{esc(url(cfg, "/orgaos/" + o["slug"] + "/"))}">'
        f"{esc(o['orgao'])} · {o['total_atributos']}</a></li>"
        for o in catalogo["orgaos"]
    )
    corpo = (
        f'<span class="chapeu">Índice</span>'
        f"<h1>Órgãos anuentes do Catálogo de Produtos</h1>"
        f'<p class="lede">Quais órgãos exigem atributos no Catálogo de Produtos '
        f"do Portal Único, e quantos cada um exige.</p>"
        f'<ul class="limpa">{itens}</ul>'
    )
    titulo = "Órgãos anuentes — Catálogo de Produtos do Portal Único"
    descricao = (
        "Índice dos órgãos que exigem atributos no Catálogo de Produtos, "
        "com o número de atributos de cada um."
    )
    caminhos.append(
        escrever(
            "orgaos/index.html",
            pagina(
                cfg,
                s,
                corpo,
                titulo,
                descricao,
                "/orgaos/",
                [("Início", "/"), ("Órgãos", "/orgaos/")],
            ),
            assinatura=assinatura_dados(
                titulo,
                descricao,
                {
                    "orgaos": [
                        [o["slug"], o["orgao"], o["total_atributos"]]
                        for o in catalogo["orgaos"]
                    ],
                },
            ),
        )
    )
    return caminhos


def gerar_privacidade(cfg, s):
    """Página de privacidade.

    O site não coleta nada no servidor (o mailto abre o cliente do visitante),
    mas roda analítica - e para um público de compliance a ausência da página
    custa mais credibilidade do que o esforco de escreve-la.
    """
    corpo = template("privacidade.html")
    titulo = "Privacidade — Sentinela do Catálogo"
    descricao = (
        "O que este site coleta: nada no servidor, analítica sem cookie, e o "
        "que é feito com o e-mail de quem escreve."
    )
    return [
        escrever(
            "privacidade/index.html",
            pagina(
                cfg,
                s,
                corpo,
                titulo,
                descricao,
                "/privacidade/",
                [("Início", "/"), ("Privacidade", "/privacidade/")],
            ),
            assinatura=titulo + descricao + corpo,
        )
    ]


def gerar_404(cfg, s):
    """O Pages serve 404.html da raiz publicada para qualquer caminho ausente.

    Fica FORA do sitemap e sai com noindex e sem canonical (caminho=None):
    é página de erro, não de conteúdo.
    """
    corpo = preencher(template("404.html"), {"base": esc(prefixo(cfg))})
    escrever(
        "404.html",
        pagina(
            cfg,
            s,
            corpo,
            "Página não encontrada — Sentinela do Catálogo",
            "A página que você procurava não existe ou saiu da lista.",
            caminho=None,
        ),
    )


# ---------------------------------------------------------------- estáticos


def _png_solido(largura, altura, blocos, fundo):
    """PNG mínimo, sem dependência: fundo sólido mais retângulos.

    Suficiente para o og:image - WhatsApp e LinkedIn não renderizam preview
    sem uma imagem raster, e SVG não serve para eles.
    """
    # Montado por faixas, nao pixel a pixel: 1200x630 em Python puro seriam
    # 756 mil iteracoes a cada build.
    vazia = bytes(fundo) * largura
    linhas = []
    for y in range(altura):
        na_faixa = [b for b in blocos if b[1] <= y < b[1] + b[3]]
        if not na_faixa:
            linhas.append(vazia)
            continue
        linha = bytearray(vazia)
        for bx, _, bw, _, bc in na_faixa:
            linha[bx * 3 : (bx + bw) * 3] = bytes(bc) * bw
        linhas.append(bytes(linha))

    cru = b"".join(b"\x00" + linha for linha in linhas)

    def pedaco(tipo, dados):
        corpo = tipo + dados
        return (
            struct.pack(">I", len(dados))
            + corpo
            + struct.pack(">I", zlib.crc32(corpo) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + pedaco(b"IHDR", struct.pack(">IIBBBBB", largura, altura, 8, 2, 0, 0, 0))
        + pedaco(b"IDAT", zlib.compress(cru, 9))
        + pedaco(b"IEND", b"")
    )


ESCURO, LARANJA = (30, 30, 30), (255, 127, 39)
# As três faixas do favicon.svg numa grade de 32: (x, y, largura, altura).
# O PNG de cada tamanho é esta geometria escalada.
FAIXAS_SELO = ((6, 8, 20, 4), (6, 15, 14, 4), (6, 22, 7, 4))


def _gravar_png(nome, largura, altura, blocos):
    os.makedirs(DIR_SITE, exist_ok=True)
    with open(os.path.join(DIR_SITE, nome), "wb") as f:
        f.write(_png_solido(largura, altura, blocos, ESCURO))


def _selo_png(nome, lado):
    """Favicon raster de lado x lado: as faixas do SVG escaladas da grade 32."""
    k = lado / 32
    blocos = [
        (round(x * k), round(y * k), round(w * k), round(h * k), LARANJA)
        for x, y, w, h in FAIXAS_SELO
    ]
    _gravar_png(nome, lado, lado, blocos)


def gerar_imagens():
    """og:image e favicons, com a marca: tres faixas encurtando = o prazo.

    O SVG é o favicon principal; o PNG de 32 cobre navegador que não lê SVG
    em rel=icon e o de 180 é o apple-touch-icon, que o iOS só aceita raster.
    """
    # As três faixas do selo, ampliadas. 100%, 68% e 36% de um vão de 660px.
    blocos = []
    topo, altura_faixa, intervalo, esquerda, vao = 210, 44, 40, 96, 660
    for i, fracao in enumerate((1.0, 0.68, 0.36)):
        y = topo + i * (altura_faixa + intervalo)
        blocos.append((esquerda, y, int(vao * fracao), altura_faixa, LARANJA))
    blocos.append((0, 606, 1200, 24, LARANJA))
    _gravar_png("og.png", 1200, 630, blocos)
    _selo_png("favicon-32.png", 32)
    _selo_png("apple-touch-icon.png", 180)

    faixas = "".join(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#ff7f27"/>'
        for x, y, w, h in FAIXAS_SELO
    )
    favicon = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="6" fill="#1e1e1e"/>'
        f"{faixas}</svg>"
    )
    escrever("favicon.svg", favicon)


def gerar_estaticos(cfg):
    """CSS e JS em arquivo externo, com hash de conteúdo no nome.

    Inline eles eram 16,5 KB repetidos em cada página - 72% dos bytes do
    site. A requisição a mais não existe na prática: a folha de fontes já
    era externa e bloqueante.

    Só o CSS perde os comentários: a expressão que os remove não entende
    string nem regex, e em JS um "/*" dentro de aspas ou de uma regex
    engoliria código. O app.js é servido byte a byte como está no template.
    """
    for chave, origem, destino in (
        ("css", "estilo.css", "estilo"),
        ("js", "app.js", "app"),
    ):
        with open(os.path.join(DIR_TEMPLATES, origem), encoding="utf-8") as f:
            conteudo = f.read()
        if chave == "css":
            # Os comentários explicam o código para quem mantém, não para
            # quem visita: 3,8 KB deles viajavam em cada página.
            conteudo = re.sub(r"/\*.*?\*/", "", conteudo, flags=re.S)
            conteudo = re.sub(r"\n{3,}", "\n\n", conteudo).strip() + "\n"
        marca = hashlib.sha256(conteudo.encode("utf-8")).hexdigest()[:8]
        nome = f"{destino}.{marca}.{chave}"
        escrever(nome, conteudo)
        ESTATICOS[chave] = "/" + nome


def gerar_fontes(cfg):
    """Copia as fontes auto-hospedadas (e as licenças OFL) para site/.

    Auto-hospedadas de propósito: a única requisição a terceiro que o site
    faz é a do GoatCounter, declarada na página de privacidade - carregar do
    Google Fonts acrescentaria outra, sem estar declarada. São fontes
    variáveis: um arquivo por subset serve todos os pesos.
    """
    if not os.path.isdir(DIR_FONTES):
        return
    destino = os.path.join(DIR_SITE, "fontes")
    os.makedirs(destino, exist_ok=True)
    marca = prefixo(cfg)
    for nome in os.listdir(DIR_FONTES):
        origem = os.path.join(DIR_FONTES, nome)
        if nome.endswith(".css"):
            with open(origem, encoding="utf-8") as f:
                css = f.read()
            # O prefixo de base_path não alcanca arquivo externo ao HTML.
            if marca:
                css = css.replace("url(/fontes/", f"url({marca}/fontes/")
            with open(
                os.path.join(destino, nome), "w", encoding="utf-8", newline=chr(10)
            ) as f:
                f.write(css)
        else:
            shutil.copy2(origem, os.path.join(destino, nome))


def gerar_cname(cfg):
    """O Pages exige CNAME na raiz publicada, e o site/ é apagado a cada build."""
    dominio = cfg.get("dominio")
    if dominio:
        escrever("CNAME", dominio + chr(10))


def gerar_indexnow(cfg):
    """Chave do IndexNow. O ping em si sai do workflow, com a lista de URLs
    que realmente mudaram (ver mudancas.txt)."""
    chave = cfg.get("indexnow_key")
    if chave:
        escrever(chave + ".txt", chave)


def gerar_feed(cfg, s):
    """RSS das viradas agendadas.

    E a única forma de push que este teste entrega: quem acompanha comex por
    leitor de feed passa a ser avisado sem precisar visitar a página.

    O pubDate de cada item é a primeira data em que a virada apareceu no
    histórico (depois a vigência do vínculo, depois a data da coleta): com a
    data da coleta em todos, o leitor de feed via 14 itens novos por dia.
    O lastBuildDate continua sendo a data da coleta.
    """
    ref = s["data_referencia"]
    vista = primeira_vista(date.fromisoformat(ref))
    pub = data_rfc822(ref)

    itens = []
    for v in s["viradas"]:
        titulo = (
            f"NCM {v['ncm']}: {v['nome'] or v['atributo']} "
            f"vira obrigatório em {br(v['vira_obrigatorio_em'])}"
        )
        desc = (
            f"O atributo {v['atributo']} ({v['nome'] or 'sem nome'}), exigido por "
            f"{'/'.join(v['orgaos']) or 'órgão não identificado'}, deixa de ser "
            f"opcional em {por_extenso(v['vira_obrigatorio_em'])}. Produtos da NCM "
            f"{v['ncm']} sem ele preenchido são desativados no Catálogo de Produtos."
        )
        link = absoluta(cfg, f"/ncm/{v['ncm']}/")
        guid = f"{link}#{v['atributo']}-{v['vira_obrigatorio_em']}"
        chave = (v["ncm"], v["atributo"], v["vira_obrigatorio_em"])
        # vigente_desde vem cru do arquivo oficial; só serve se for data.
        quando = next(
            (d for d in (vista.get(chave), v.get("vigente_desde")) if data_valida(d)), ref
        )
        itens.append(
            f"<item><title>{esc(titulo)}</title><link>{esc(link)}</link>"
            f'<guid isPermaLink="false">{esc(guid)}</guid>'
            f"<description>{esc(desc)}</description>"
            f"<pubDate>{data_rfc822(quando)}</pubDate></item>"
        )

    escrever(
        "feed.xml",
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">'
        "<channel>"
        "<title>Sentinela do Catálogo — viradas de atributo por NCM</title>"
        f"<link>{esc(absoluta(cfg, '/'))}</link>"
        f'<atom:link href="{esc(absoluta(cfg, "/feed.xml"))}" rel="self" '
        'type="application/rss+xml"/>'
        "<description>Atributos do Catálogo de Produtos do Portal Único que "
        "têm data marcada para virar obrigatórios.</description>"
        "<language>pt-BR</language>"
        f"<lastBuildDate>{pub}</lastBuildDate>"
        f"{''.join(itens)}"
        "</channel></rss>",
    )


def ler_lastmod():
    """O lastmod.json da geração anterior; vazio se não existe ou é ilegível
    (o custo de um arquivo perdido é um dia de lastmod = hoje, não um build
    quebrado)."""
    if not os.path.exists(ARQ_LASTMOD):
        return {}
    try:
        with open(ARQ_LASTMOD, encoding="utf-8") as f:
            anterior = json.load(f)
    except (OSError, ValueError):
        return {}
    return anterior if isinstance(anterior, dict) else {}


def calcular_lastmod(anterior, paginas, hoje):
    """lastmod honesto: a data em que a página mudou pela última vez.

    Carimbar hoje nas 10 mil URLs todo dia é a mentira que faz o Google parar
    de acreditar no lastmod - e quem perde são exatamente as poucas páginas
    que mudaram de verdade. Pura: anterior é o mapa lido de lastmod.json,
    paginas é {caminho: hash}; devolve (mapa_novo, lista_de_mudadas).
    """
    atual, mudadas = {}, []
    for caminho, marca in paginas.items():
        antes = anterior.get(caminho)
        if isinstance(antes, list) and len(antes) == 2 and antes[0] == marca:
            atual[caminho] = antes
        else:
            atual[caminho] = [marca, hoje]
            mudadas.append(caminho)
    return atual, mudadas


def gravar_lastmod(atual):
    """Grava lastmod.json atomicamente (tmp + os.replace)."""
    temporario = ARQ_LASTMOD + ".tmp"
    with open(temporario, "w", encoding="utf-8", newline="\n") as f:
        json.dump(atual, f, ensure_ascii=False, sort_keys=True, indent=0)
        f.write("\n")
    os.replace(temporario, ARQ_LASTMOD)


def gerar_sitemap(cfg, caminhos, s, datas, mudadas):
    """Índice de sitemaps: 10 mil URLs num arquivo só é legal, mas ilegível.

    datas é o mapa de calcular_lastmod ({caminho: [hash, data]}); mudadas, a
    lista de caminhos cujo conteúdo mudou nesta geração.
    """
    hoje = s["data_referencia"]

    ordenados = sorted(set(caminhos))
    grupos = {"ncm": [], "atributos": [], "geral": []}
    for c in ordenados:
        if c.startswith("/ncm/"):
            grupos["ncm"].append(c)
        elif c.startswith("/atributos/"):
            grupos["atributos"].append(c)
        else:
            grupos["geral"].append(c)

    arquivos = []
    for nome, lista in grupos.items():
        if not lista:
            continue
        blocos = [lista[i : i + POR_SITEMAP] for i in range(0, len(lista), POR_SITEMAP)]
        for numero, bloco in enumerate(blocos, start=1):
            arquivo = f"sitemap-{nome}{'' if len(blocos) == 1 else f'-{numero}'}.xml"
            lastmods = {c: datas.get(c, [None, hoje])[1] for c in bloco}
            urls = "".join(
                f"<url><loc>{esc(absoluta(cfg, c))}</loc>"
                f"<lastmod>{lastmods[c]}</lastmod></url>"
                for c in bloco
            )
            escrever(
                arquivo,
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"{urls}</urlset>",
            )
            # O lastmod do sub-sitemap é o da URL mais recente dele: carimbar
            # hoje no índice desmentia o lastmod honesto das URLs de dentro.
            arquivos.append((arquivo, max(lastmods.values())))

    indice = "".join(
        f"<sitemap><loc>{esc(absoluta(cfg, '/' + a))}</loc>"
        f"<lastmod>{quando}</lastmod></sitemap>"
        for a, quando in arquivos
    )
    escrever(
        "sitemap.xml",
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{indice}</sitemapindex>",
    )

    escrever(
        "robots.txt",
        f"User-agent: *\nAllow: /\n\nSitemap: {absoluta(cfg, '/sitemap.xml')}\n",
    )
    escrever(".nojekyll", "")

    # O workflow lê este arquivo para pingar o IndexNow só com o que mudou.
    # Acima do teto isso não é "mudou conteúdo", é rebuild (primeira geração,
    # troca de template, lastmod.json perdido). Mandar 11 mil URLs nesse caso
    # é ruído que só queima a credibilidade do ping.
    if len(mudadas) > TETO_INDEXNOW:
        aviso = sorted(mudadas)[:1]
        escrever("mudancas.txt", "".join(absoluta(cfg, c) + "\n" for c in aviso))
    else:
        escrever("mudancas.txt", "".join(absoluta(cfg, c) + "\n" for c in sorted(mudadas)))


def versoes_divergem(s, catalogo, completo):
    """Os três arquivos têm de vir da mesma colheita.

    ultimo.json e atributos.json são versionados; completo.json não é. Um
    completo.json de ontem com um ultimo.json de hoje produz páginas de NCM
    que contradizem a home - e o build seguiria com exit 0. Devolve a lista
    (nome, versao) quando divergem, vazia quando são iguais.
    """
    versoes = [
        ("ultimo.json", (s.get("contagens") or {}).get("versao")),
        ("atributos.json", catalogo.get("versao")),
        ("completo.json", completo.get("versao")),
    ]
    if len({v for _, v in versoes}) == 1:
        return []
    return versoes


def main():
    for arquivo, quem in (
        (ARQ_ULTIMO, "coletor.py"),
        (ARQ_ATRIBUTOS, "coletor.py"),
        (ARQ_COMPLETO, "coletor.py"),
    ):
        if not os.path.exists(arquivo):
            print(f"ERRO: {arquivo} nao existe. Rode {quem} antes.", file=sys.stderr)
            return 1
    with open(ARQ_ULTIMO, encoding="utf-8") as f:
        s = json.load(f)
    with open(ARQ_ATRIBUTOS, encoding="utf-8") as f:
        catalogo = json.load(f)
    with open(ARQ_COMPLETO, encoding="utf-8") as f:
        completo = json.load(f)

    # Antes de apagar site/: um site de ontem no ar é melhor que nenhum.
    divergentes = versoes_divergem(s, catalogo, completo)
    if divergentes:
        detalhe = ", ".join(f"{nome}={versao!r}" for nome, versao in divergentes)
        print(
            f"ERRO: os arquivos de dados não são da mesma colheita ({detalhe}). "
            f"Rode coletor.py de novo.",
            file=sys.stderr,
        )
        return 1

    cfg = config()
    PAGINAS.clear()
    if os.path.isdir(DIR_SITE):
        shutil.rmtree(DIR_SITE)

    com_pagina = {a["codigo"] for a in catalogo["atributos"]}
    # Toda NCM do mapa completo ganha página; é o que decide link ou texto.
    ncms_com_pagina = set(completo.get("ncms", {}))
    gerar_estaticos(cfg)
    caminhos = []
    caminhos += gerar_index(cfg, s, ncms_com_pagina)
    caminhos += gerar_ncms(cfg, s, completo, com_pagina)
    caminhos += gerar_atributos(cfg, s, catalogo, com_pagina, ncms_com_pagina)
    caminhos += gerar_orgaos(cfg, s, catalogo)
    caminhos += gerar_privacidade(cfg, s)
    gerar_404(cfg, s)
    gerar_feed(cfg, s)
    gerar_imagens()
    gerar_fontes(cfg)
    gerar_cname(cfg)
    gerar_indexnow(cfg)
    datas, mudadas = calcular_lastmod(ler_lastmod(), PAGINAS, s["data_referencia"])
    gravar_lastmod(datas)
    gerar_sitemap(cfg, caminhos, s, datas, mudadas)

    print(
        f"{len(caminhos)} paginas geradas em site/ "
        f"(referencia {br(s['data_referencia'])}, {len(s['viradas'])} viradas)"
    )
    print(f"{len(mudadas)} URLs com conteudo novo desde a ultima geracao")
    if not (cfg.get("form_embed_url") or cfg.get("contato_email")):
        print(
            "AVISO: captura nao configurada - defina contato_email (mailto) ou "
            "form_embed_url em config.json. Sem isso o teste nao produz metrica."
        )
    if not cfg.get("goatcounter_code"):
        print(
            "AVISO: goatcounter_code vazio - sem analitica, zero cadastro nao "
            "distingue 'ninguem quer' de 'ninguem viu'."
        )
    if not cfg.get("base_url"):
        print(
            "AVISO: base_url vazio em config.json - "
            "canonical e sitemap saem com caminho relativo."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
