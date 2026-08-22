"""
Gera o site estatico a partir de dados/ultimo.json.

Nao acessa a rede. Roda depois do coletor, e pode ser rodado sozinho para
recompor o site sem baixar nada de novo.

Saida em site/:
    index.html
    ncm/<NCM>/index.html        uma por NCM com virada agendada
    atributos/<CODIGO>/index.html
    ncm/index.html              indice
    atributos/index.html        indice
    sitemap.xml, robots.txt
"""

import json
import os
import urllib.parse
import re
import shutil
import sys
from datetime import date, datetime, timezone
from email.utils import formatdate

RAIZ = os.path.dirname(os.path.abspath(__file__))
DIR_TEMPLATES = os.path.join(RAIZ, "templates")
DIR_SITE = os.path.join(RAIZ, "site")
DIR_HISTORICO = os.path.join(RAIZ, "dados", "historico")
ARQ_ULTIMO = os.path.join(RAIZ, "dados", "ultimo.json")
ARQ_CONFIG = os.path.join(RAIZ, "config.json")
ARQ_ATRIBUTOS = os.path.join(RAIZ, "dados", "atributos.json")
DIR_FONTES = os.path.join(RAIZ, "fontes")

MESES = ("", "janeiro", "fevereiro", "março", "abril", "maio", "junho",
         "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")


def config():
    padrao = {"base_url": "", "base_path": "", "form_embed_url": "",
              "contato_email": "", "goatcounter_code": "",
              "dominio": "", "indexnow_key": ""}
    if os.path.exists(ARQ_CONFIG):
        with open(ARQ_CONFIG, encoding="utf-8") as f:
            padrao.update(json.load(f))
    return padrao


def esc(texto):
    if texto is None:
        return ""
    return (str(texto).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def template(nome):
    with open(os.path.join(DIR_TEMPLATES, nome), encoding="utf-8") as f:
        return f.read()


def preencher(texto, valores):
    for chave, valor in valores.items():
        texto = texto.replace("{{" + chave + "}}", str(valor))
    return texto


def br(iso):
    """2026-08-30 -> 30/08/2026"""
    if not iso:
        return ""
    a, m, d = iso.split("-")
    return f"{d}/{m}/{a}"


def plural(n, singular, plural_form):
    """1 -> 'vinculo', 2 -> 'vinculos'. Evita 'Sao 1 vinculos em 1 NCMs'."""
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


def escrever(caminho_relativo, html):
    destino = os.path.join(DIR_SITE, caminho_relativo)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(destino, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    return "/" + caminho_relativo.replace("\\", "/").replace("index.html", "")


def bloco_formulario(cfg):
    url = cfg.get("form_embed_url")
    if url:
        return (f'<iframe src="{esc(url)}" loading="lazy" '
                f'title="Cadastro para receber os avisos"></iframe>')

    email = cfg.get("contato_email")
    if email:
        assunto = "Quero acompanhar minhas NCMs"
        # Sem escapes de nova linha aqui: montado por join para nao quebrar
        # em nenhuma camada de shell ou heredoc.
        corpo = chr(10).join([
            "Minhas NCMs (uma por linha):", "", "", "",
            "--",
            "Quantos SKUs voce mantem no catalogo?",
            "Como voce descobre hoje que um atributo vai virar obrigatorio?",
        ])
        href = (f"mailto:{email}"
                f"?subject={urllib.parse.quote(assunto)}"
                f"&body={urllib.parse.quote(corpo)}")
        return (f'<p><a class="botao" href="{esc(href)}">'
                f'Enviar minhas NCMs por e-mail</a></p>'
                f'<p style="font-size:.86rem;color:var(--faint);margin-top:12px">'
                f'Abre seu cliente de e-mail com a mensagem pronta. '
                f'Sem cadastro e sem senha.</p>')

    return ('<p class="pendente">[captura ainda não configurada — '
            'definir contato_email ou form_embed_url em config.json]</p>')


def bloco_analytics(cfg):
    codigo = cfg.get("goatcounter_code")
    if not codigo:
        return ""
    return ('<script data-goatcounter="'
            f'https://{esc(codigo)}.goatcounter.com/count"'
            ' async src="//gc.zgo.at/count.js"></script>')


def bloco_jsonld(dados):
    if not dados:
        return ""
    return ('<script type="application/ld+json">'
            + json.dumps(dados, ensure_ascii=False) + '</script>')


def trilha(cfg, itens):
    """BreadcrumbList - o dado estruturado com retorno visivel no resultado."""
    base = (cfg.get("base_url", "").rstrip("/")
            + cfg.get("base_path", "").rstrip("/"))
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": nome,
             "item": base + caminho}
            for i, (nome, caminho) in enumerate(itens)
        ],
    }


def pagina(cfg, snapshot, corpo, titulo, descricao, caminho, jsonld=None):
    base = template("base.html")
    canonical = (cfg.get("base_url", "").rstrip("/")
                 + cfg.get("base_path", "").rstrip("/") + caminho) or caminho
    html = preencher(base, {
        "titulo": esc(titulo),
        "descricao": esc(descricao),
        "canonical": esc(canonical),
        "conteudo": corpo,
        "coletado_em": br(snapshot["data_referencia"]),
        "versao": esc(snapshot["contagens"]["versao"]),
        "formulario": bloco_formulario(cfg),
        "analytics": bloco_analytics(cfg) + bloco_jsonld(jsonld),
        "feed": "/feed.xml",  # o prefixo de base_path e aplicado depois
    })
    # Em repositorio de projeto o Pages serve sob /<repo>/, entao todo link
    # interno precisa do prefixo. Com dominio proprio, base_path fica vazio.
    prefixo = cfg.get("base_path", "").rstrip("/")
    if prefixo:
        html = html.replace('href="/', f'href="{prefixo}/')
    return html


# ---------------------------------------------------------------- fragmentos

def tabela_viradas(viradas, referencia):
    if not viradas:
        return ('<div class="rolagem"><table><tbody><tr><td>'
                'Nenhuma virada agendada no arquivo de hoje.'
                '</td></tr></tbody></table></div>')
    linhas = []
    for v in viradas:
        dias = dias_ate(v["vira_obrigatorio_em"], referencia)
        prazo = "hoje" if dias == 0 else ("amanhã" if dias == 1 else f"em {dias} dias")
        # A barra da a leitura visual do prazo: 30 dias enche, hoje quase vazia.
        largura = min(100, max(6, round(dias / 30 * 100)))
        # Urgencia acende so abaixo de 7 dias - por isso significa algo.
        urg = " urgente" if dias <= 7 else ""
        linhas.append(
            f'<tr>'
            f'<td class="ncm"><a href="/ncm/{v["ncm"]}/">{esc(v["ncm"])}</a></td>'
            f'<td><a href="/atributos/{v["atributo"]}/">{esc(v["nome"] or v["atributo"])}</a>'
            f'<br><span class="cod-inline">{esc(v["atributo"])}</span></td>'
            f'<td>{esc("/".join(v["orgaos"]) or "—")}</td>'
            f'<td class="data">{br(v["vira_obrigatorio_em"])}'
            f'<br><span class="prazo-txt{urg}">{prazo}</span>'
            f'<span class="prazo{urg}"><i style="--w:{largura}%"></i></span>'
            f'</td>'
            f'</tr>')
    return ('<div class="rolagem"><table>'
            '<thead><tr><th>NCM</th><th>Atributo</th><th>Órgão</th>'
            '<th>Vira obrigatório em</th></tr></thead>'
            f'<tbody>{"".join(linhas)}</tbody></table></div>')


def tabela_atributos_ncm(atributos):
    linhas = []
    for a in atributos:
        if a["vira_obrigatorio_em"]:
            marca = f'<span class="tag muda">vira obrigatório em {br(a["vira_obrigatorio_em"])}</span>'
        elif a["obrigatorio"]:
            marca = '<span class="tag obr">obrigatório</span>'
        else:
            marca = '<span class="tag opc">opcional</span>'
        linhas.append(
            f'<tr>'
            f'<td class="cod"><a href="/atributos/{a["codigo"]}/">{esc(a["codigo"])}</a></td>'
            f'<td>{esc(a["nome"] or "—")}</td>'
            f'<td>{esc("/".join(a["orgaos"]) or "—")}</td>'
            f'<td>{esc(a["modalidade"] or "—")}</td>'
            f'<td>{marca}</td>'
            f'</tr>')
    return ('<div class="rolagem"><table>'
            '<thead><tr><th>Código</th><th>Atributo</th><th>Órgão</th>'
            '<th>Modalidade</th><th>Situação</th></tr></thead>'
            f'<tbody>{"".join(linhas)}</tbody></table></div>')


def bloco_historico(ncms_com_pagina=frozenset()):
    """O que mudou nos ultimos 30 dias, montado do arquivo diario.

    O endpoint oficial ignora ?data= e nao serve versoes passadas: sem este
    arquivo local nao existe 'o que mudou'. E tambem o que impede a pagina de
    ficar vazia entre um lote de viradas e o proximo.
    """
    if not os.path.isdir(DIR_HISTORICO):
        return ""
    arquivos = sorted(f for f in os.listdir(DIR_HISTORICO) if f.endswith(".json"))
    if len(arquivos) < 2:
        return ""

    atual = json.load(open(os.path.join(DIR_HISTORICO, arquivos[-1]), encoding="utf-8"))
    anteriores = arquivos[-31:-1]
    vistos_antes = set()
    for nome in anteriores:
        s = json.load(open(os.path.join(DIR_HISTORICO, nome), encoding="utf-8"))
        vistos_antes |= {(v["ncm"], v["atributo"]) for v in s["viradas"]}

    agora = {(v["ncm"], v["atributo"]): v for v in atual["viradas"]}
    novas = [v for k, v in agora.items() if k not in vistos_antes]
    sumiram = sorted(vistos_antes - set(agora))

    if not novas and not sumiram:
        return ""

    partes = ["<h2>O que mudou nos últimos 30 dias</h2>"]
    if novas:
        itens = "".join(
            (f'<li><a href="/ncm/{v["ncm"]}/">{esc(v["ncm"])}</a> — '
             if v["ncm"] in ncms_com_pagina else f'<li>{esc(v["ncm"])} — ')
            + f'{esc(v["nome"] or v["atributo"])}, a partir de {br(v["vira_obrigatorio_em"])}</li>'
            for v in sorted(novas, key=lambda x: x["vira_obrigatorio_em"]))
        partes.append(f"<h3>Viradas novas</h3><ul>{itens}</ul>")
    if sumiram:
        # NCM que saiu da lista nao tem mais pagina gerada - nao linkar.
        itens = "".join(
            (f'<li><a href="/ncm/{n}/">{esc(n)}</a> — {esc(c)}</li>'
             if n in ncms_com_pagina else f'<li>{esc(n)} — {esc(c)}</li>')
            for n, c in sumiram)
        partes.append(
            "<h3>Saíram da lista</h3><p style='font-size:.92rem;color:var(--muted)'>"
            "Já passaram da data ou foram removidas pela Receita.</p>"
            f"<ul>{itens}</ul>")
    return "".join(partes)


# ---------------------------------------------------------------- paginas

def gerar_index(cfg, s):
    ref = date.fromisoformat(s["data_referencia"])
    vs = s["viradas"]
    caminhos = []

    if vs:
        proxima = vs[0]["vira_obrigatorio_em"]
        dias = dias_ate(proxima, ref)
        ncms = len({v["ncm"] for v in vs})
        orgaos = sorted({o for v in vs for o in v["orgaos"]})
        datas = sorted({v["vira_obrigatorio_em"] for v in vs})
        no_corte = sum(1 for v in vs if v["vira_obrigatorio_em"] == proxima)
        seguinte = br(datas[1]) if len(datas) > 1 else "—"
        cartao = (
            '<div class="contagem-cartao">'
            '<div class="contagem-topo">'
            f'<span class="contagem-num" data-contagem="{dias}">{dias}</span>'
            f'<span class="contagem-un">{plural(dias, 'dia', 'dias')}</span>'
            '</div>'
            '<div class="contagem-fatos">'
            f'<div><span>corte</span>{br(proxima)}</div>'
            f'<div><span>órgão</span>{esc('/'.join(orgaos))}</div>'
            f'<div><span>vínculos</span>{no_corte} de {len(vs)}</div>'
            f'<div><span>próximo</span>{seguinte}</div>'
            '</div></div>')
        h1 = (f"{len(vs)} {plural(len(vs), 'atributo', 'atributos')} de NCM "
              f"{plural(len(vs), 'vira', 'viram')} "
              f"{plural(len(vs), 'obrigatório', 'obrigatórios')} "
              f"{plural(max(dias, 1), 'amanhã', f'nos próximos {max(dias, 1)} dias')}")
        lede = (f"{plural(len(vs), 'É', 'São')} {len(vs)} "
                f"{plural(len(vs), 'vínculo', 'vínculos')} em {ncms} "
                f"{plural(ncms, 'NCM', 'NCMs')}. "
                f"Os produtos {plural(ncms, 'dessa NCM', 'dessas NCMs')} que estiverem "
                f"sem {plural(len(vs), 'o atributo preenchido', 'os atributos preenchidos')} "
                f"na data são desativados no Catálogo de Produtos do Portal Único.")
        descricao = (f"{len(vs)} {plural(len(vs), 'atributo', 'atributos')} em "
                     f"{ncms} {plural(ncms, 'NCM', 'NCMs')} {plural(len(vs), 'vira', 'viram')} "
                     f"{plural(len(vs), 'obrigatório', 'obrigatórios')} no Catálogo de "
                     f"Produtos do Portal Único. Próximo corte em {br(proxima)}.")
    else:
        h1 = "Nenhum atributo de NCM tem virada agendada hoje"
        lede = ("O arquivo oficial de hoje não traz nenhum vínculo com data para virar "
                "obrigatório. Esta página é atualizada todo dia — quando a Receita "
                "agendar uma nova virada, ela aparece aqui.")
        cartao = (
            '<div class="contagem-cartao">'
            '<div class="contagem-fatos" style="margin:0;padding:0;border:0">'
            '<div><span>vínculos com data</span>0</div>'
            '<div><span>releitura</span>amanhã 09:00 UTC</div>'
            '</div></div>')
        descricao = ("Monitoramento diário dos atributos de NCM que viram obrigatórios "
                     "no Catálogo de Produtos do Portal Único.")

    corpo = preencher(template("index.html"), {
        "data_ref": br(s["data_referencia"]),
        "h1": esc(h1),
        "lede": esc(lede),
        "cartao": cartao,
        "tabela": tabela_viradas(vs, ref),
        "historico": bloco_historico({f["ncm"] for f in s.get("ncms_afetadas", [])}),
    })
    titulo = ("Atributos de NCM que viram obrigatórios — Catálogo do Portal Único"
              if vs else "Atributos de NCM com virada agendada — Portal Único")
    dataset = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "Atributos por NCM com virada agendada — Catálogo de Produtos",
        "description": ("Extração diária da relação oficial de atributos por NCM do "
                        "Portal Único, isolando os que têm data marcada para virar "
                        "obrigatórios."),
        "inLanguage": "pt-BR",
        "isAccessibleForFree": True,
        "creativeWorkStatus": "Published",
        "dateModified": s["data_referencia"],
        "isBasedOn": s.get("fonte"),
        "creator": {"@type": "GovernmentOrganization",
                    "name": "Portal Único de Comércio Exterior — Receita Federal"},
        "temporalCoverage": s["data_referencia"],
    }
    caminhos.append(escrever("index.html",
                             pagina(cfg, s, corpo, titulo, descricao, "/", dataset)))
    return caminhos


def gerar_ncms(cfg, s):
    ref = date.fromisoformat(s["data_referencia"])
    por_ncm = {v["ncm"]: [] for v in s["viradas"]}
    for v in s["viradas"]:
        por_ncm[v["ncm"]].append(v)
    caminhos = []

    for ficha in s["ncms_afetadas"]:
        ncm = ficha["ncm"]
        vs = sorted(por_ncm.get(ncm, []), key=lambda x: x["vira_obrigatorio_em"])
        if not vs:
            continue
        proxima = vs[0]["vira_obrigatorio_em"]
        dias = dias_ate(proxima, ref)
        nomes = sorted({v["nome"] or v["atributo"] for v in vs})
        orgaos = sorted({o for v in vs for o in v["orgaos"]})

        h1 = (f"NCM {ncm}: {len(vs)} atributo{'s' if len(vs) > 1 else ''} "
              f"vira{'m' if len(vs) > 1 else ''} obrigatório em {br(proxima)}")
        lede = (f"Produtos classificados na NCM {ncm} sem "
                f"{'esses atributos' if len(vs) > 1 else 'esse atributo'} preenchido"
                f"{'s' if len(vs) > 1 else ''} serão desativados no Catálogo de Produtos "
                f"a partir de {por_extenso(proxima)}.")
        itens = "".join(f"<li><strong>{esc(n)}</strong></li>" for n in nomes)
        aviso = (f'<div class="aviso"><strong>'
                 f'{"É hoje." if dias == 0 else ("Falta 1 dia." if dias == 1 else f"Faltam {dias} dias.")}'
                 f'</strong> '
                 f'Exigência {"do " + orgaos[0] if len(orgaos) == 1 else "dos órgãos anuentes"}. '
                 f'Atributos afetados:<ul style="margin:8px 0 0">{itens}</ul></div>')
        descricao = (f"NCM {ncm}: {len(vs)} atributo(s) do Catálogo de Produtos do Portal "
                     f"Único viram obrigatórios em {br(proxima)}. Lista completa dos "
                     f"atributos exigidos para esta NCM.")

        corpo = preencher(template("ncm.html"), {
            "ncm": esc(ncm),
            "h1": esc(h1),
            "lede": esc(lede),
            "aviso": aviso,
            "tabela": tabela_atributos_ncm(ficha["atributos"]),
        })
        titulo = f"NCM {ncm} — atributos que viram obrigatórios em {br(proxima)}"
        caminhos.append(escrever(f"ncm/{ncm}/index.html", pagina(
            cfg, s, corpo, titulo, descricao, f"/ncm/{ncm}/",
            trilha(cfg, [("Início", "/"), ("NCMs", "/ncm/"),
                         (f"NCM {ncm}", f"/ncm/{ncm}/")]))))

    # indice
    itens = "".join(
        f'<li><a href="/ncm/{f["ncm"]}/">{esc(f["ncm"])}</a></li>'
        for f in s["ncms_afetadas"])
    corpo = (f'<span class="chapeu">Índice</span>'
             f'<h1>NCMs com virada agendada</h1>'
             f'<p class="lede">As {len(s["ncms_afetadas"])} NCMs que hoje têm atributo '
             f'com data marcada para virar obrigatório.</p>'
             f'<ul class="limpa">{itens}</ul>')
    caminhos.append(escrever("ncm/index.html", pagina(
        cfg, s, corpo, "NCMs com atributo virando obrigatório — Portal Único",
        "Índice das NCMs com atributos que viram obrigatórios no Catálogo de Produtos.",
        "/ncm/")))
    return caminhos


def gerar_atributos(cfg, s, catalogo):
    caminhos = []
    virando = {}
    for v in s["viradas"]:
        virando.setdefault(v["atributo"], []).append(v)

    for a in catalogo["atributos"]:
        cod = a["codigo"]
        nome = a.get("nome") or cod
        vs = virando.get(cod, [])

        if vs:
            data = min(v["vira_obrigatorio_em"] for v in vs)
            ncms_v = sorted({v["ncm"] for v in vs})
            lista = "".join(f'<li><a href="/ncm/{n}/">{esc(n)}</a></li>' for n in ncms_v)
            aviso = (f'<div class="aviso"><strong>Este atributo vira obrigatório em '
                     f'{br(data)}</strong> para {len(ncms_v)} NCM'
                     f'{"s" if len(ncms_v) > 1 else ""}:'
                     f'<ul class="limpa" style="margin-top:10px">{lista}</ul></div>')
            h1 = f"{nome} ({cod}): vira obrigatório em {br(data)}"
            descricao = (f"{nome} ({cod}) no Catálogo de Produtos do Portal Único: "
                         f"o que preencher, opções válidas e em quais NCMs se aplica. "
                         f"Vira obrigatório em {br(data)}.")
        else:
            aviso = ""
            h1 = f"{nome} ({cod}): o que preencher no Catálogo de Produtos"
            descricao = (f"{nome} ({cod}) no Catálogo de Produtos do Portal Único: "
                         f"o que preencher, opções válidas e em quais NCMs se aplica.")

        lista_orgaos = a.get("orgaos") or []
        orgaos = "/".join(lista_orgaos) or "—"
        forma = {"LISTA_ESTATICA": "lista de opções", "BOOLEANO": "sim ou não",
                 "TEXTO": "texto livre", "NUMERO_REAL": "número decimal",
                 "NUMERO_INTEIRO": "número inteiro", "DATA": "data"
                 }.get(a.get("forma_preenchimento"), a.get("forma_preenchimento") or "—")
        lede = esc(f"Atributo exigido por {orgaos}, preenchido como {forma}. "
                   f"Aplica-se a {milhar(a['total_ncms'])} NCM"
                   f"{'s' if a['total_ncms'] != 1 else ''}.")

        definicao = (f"<h2>O que é</h2><p>{esc(a['definicao'])}</p>"
                     if a.get("definicao") else "")
        orientacao = (f"<h2>Orientação oficial de preenchimento</h2>"
                      f"<p>{esc(a['orientacao'])}</p>" if a.get("orientacao") else "")

        if a.get("dominio"):
            opcoes = "".join(f"<dt>{esc(d['codigo'])}</dt><dd>{esc(d['descricao'])}</dd>"
                             for d in a["dominio"])
            total_dom = a.get("dominio_total", len(a["dominio"]))
            corte = ""
            if total_dom > len(a["dominio"]):
                corte = (f'<p style="font-size:.9rem;color:var(--faint)">Mostrando as '
                         f'{milhar(len(a["dominio"]))} primeiras de '
                         f'{milhar(total_dom)} opções. A lista completa está no '
                         f'arquivo oficial.</p>')
            dominio = (f"<h2>Opções válidas ({milhar(total_dom)})</h2>"
                       f'<dl class="dominio">{opcoes}</dl>{corte}')
        else:
            dominio = ""

        mostradas = a.get("ncms") or []
        if a["total_ncms"] > len(mostradas):
            aplicacao = (f"Este atributo está vinculado a {milhar(a['total_ncms'])} NCMs. "
                         f"As {len(mostradas)} primeiras:")
        else:
            aplicacao = (f"Este atributo está vinculado a "
                         f"{milhar(a['total_ncms'])} NCM"
                         f"{'s' if a['total_ncms'] != 1 else ''}:")
        chips = "".join(f"<li><a href='/ncm/{n}/'>{esc(n)}</a></li>"
                        if any(f["ncm"] == n for f in s["ncms_afetadas"])
                        else f"<li>{esc(n)}</li>" for n in mostradas)

        if lista_orgaos:
            links = " · ".join(
                f'<a href="/orgaos/{re.sub(chr(91) + "^a-z0-9" + chr(93) + "+", "-", o.lower()).strip("-")}/">'
                f'{esc(o)}</a>' for o in lista_orgaos)
            lede = lede + f' <span style="font-size:.92rem">Ver todos os atributos de {links}.</span>'

        corpo = preencher(template("atributo.html"), {
            "codigo": esc(cod), "h1": esc(h1), "lede": lede, "aviso": aviso,
            "definicao": definicao, "orientacao": orientacao, "dominio": dominio,
            "aplicacao": esc(aplicacao),
            "ncms": f'<ul class="limpa">{chips}</ul>' if chips else "",
        })
        titulo = f"{nome} ({cod}) — Catálogo de Produtos do Portal Único"
        caminhos.append(escrever(f"atributos/{cod}/index.html", pagina(
            cfg, s, corpo, titulo, descricao, f"/atributos/{cod}/",
            trilha(cfg, [("Início", "/"), ("Atributos", "/atributos/"),
                         (nome, f"/atributos/{cod}/")]))))

    orgs = "".join(
        f'<li><a href="/orgaos/{o["slug"]}/">{esc(o["orgao"])} · '
        f'{o["total_atributos"]}</a></li>' for o in catalogo["orgaos"])
    destaque = [a for a in catalogo["atributos"] if a.get("nas_viradas")]
    itens = "".join(
        f'<li><a href="/atributos/{a["codigo"]}/">{esc(a["codigo"])} · '
        f'{esc(a.get("nome") or "")}</a></li>' for a in destaque)
    corpo = (f'<span class="chapeu">Índice</span><h1>Atributos do Catálogo de Produtos</h1>'
             f'<p class="lede">São {milhar(len(catalogo["atributos"]))} atributos com '
             f'orientação oficial de preenchimento, opções válidas e a lista de NCMs onde '
             f'se aplicam. Navegue pelo órgão que exige cada um.</p>'
             f'<h2>Por órgão anuente</h2>'
             f'<ul class="limpa">{orgs}</ul>'
             + (f'<h2>Com virada agendada</h2><ul class="limpa">{itens}</ul>'
                if itens else ""))
    caminhos.append(escrever("atributos/index.html", pagina(
        cfg, s, corpo, "Atributos do Catálogo de Produtos — Portal Único",
        "Índice dos atributos do Catálogo de Produtos do Portal Único, com opções "
        "válidas e NCMs onde se aplicam.", "/atributos/")))
    return caminhos


def gerar_orgaos(cfg, s, catalogo):
    """Uma pagina por orgao anuente.

    Cria um eixo de consulta novo ("atributos anvisa duimp") e resolve o
    problema de um indice unico com mais de mil itens.
    """
    caminhos = []
    virando = {a["codigo"] for a in catalogo["atributos"] if a.get("nas_viradas")}

    for o in catalogo["orgaos"]:
        linhas = []
        for a in o["atributos"]:
            marca = ('<span class="tag muda">virada agendada</span>'
                     if a["codigo"] in virando else "")
            forma = {"LISTA_ESTATICA": "lista de opções", "BOOLEANO": "sim ou não",
                     "TEXTO": "texto livre", "NUMERO_REAL": "número decimal",
                     "NUMERO_INTEIRO": "número inteiro", "DATA": "data"
                     }.get(a.get("forma_preenchimento"), a.get("forma_preenchimento") or "—")
            linhas.append(
                f'<tr><td class="cod"><a href="/atributos/{a["codigo"]}/">'
                f'{esc(a["codigo"])}</a></td>'
                f'<td>{esc(a.get("nome") or "—")}</td>'
                f'<td>{esc(forma)}</td>'
                f'<td class="num">{milhar(a.get("total_ncms", 0))}</td>'
                f'<td>{marca}</td></tr>')
        tabela = ('<div class="rolagem"><table><thead><tr><th>Código</th>'
                  '<th>Atributo</th><th>Preenchimento</th><th>NCMs</th>'
                  '<th></th></tr></thead>'
                  f'<tbody>{"".join(linhas)}</tbody></table></div>')

        n = o["total_atributos"]
        com_virada = [a for a in o["atributos"] if a["codigo"] in virando]
        h1 = f'Atributos exigidos pelo {o["orgao"]} no Catálogo de Produtos'
        lede = (f'O {o["orgao"]} exige {milhar(n)} '
                f'{plural(n, "atributo", "atributos")} no Catálogo de Produtos do '
                f'Portal Único. Cada um com orientação oficial de preenchimento e '
                f'as NCMs em que se aplica.')
        aviso = ""
        if com_virada:
            itens = "".join(f'<li><a href="/atributos/{a["codigo"]}/">'
                            f'{esc(a.get("nome") or a["codigo"])}</a></li>'
                            for a in com_virada)
            aviso = (f'<div class="aviso"><strong>{len(com_virada)} '
                     f'{plural(len(com_virada), "atributo deste órgão tem", "atributos deste órgão têm")} '
                     f'virada agendada:</strong>'
                     f'<ul class="limpa" style="margin-top:10px">{itens}</ul></div>')

        corpo = preencher(template("orgao.html"), {
            "h1": esc(h1), "lede": esc(lede), "aviso": aviso, "tabela": tabela,
        })
        caminhos.append(escrever(f'orgaos/{o["slug"]}/index.html', pagina(
            cfg, s, corpo,
            f'Atributos do {o["orgao"]} — Catálogo de Produtos do Portal Único',
            f'Os {milhar(n)} atributos exigidos pelo {o["orgao"]} no Catálogo de '
            f'Produtos do Portal Único, com orientação de preenchimento e NCMs.',
            f'/orgaos/{o["slug"]}/',
            trilha(cfg, [("Início", "/"), ("Órgãos", "/orgaos/"),
                         (o["orgao"], f'/orgaos/{o["slug"]}/')]))))

    itens = "".join(
        f'<li><a href="/orgaos/{o["slug"]}/">{esc(o["orgao"])} · '
        f'{o["total_atributos"]}</a></li>' for o in catalogo["orgaos"])
    corpo = (f'<span class="chapeu">Índice</span>'
             f'<h1>Órgãos anuentes do Catálogo de Produtos</h1>'
             f'<p class="lede">Quais órgãos exigem atributos no Catálogo de Produtos '
             f'do Portal Único, e quantos cada um exige.</p>'
             f'<ul class="limpa">{itens}</ul>')
    caminhos.append(escrever("orgaos/index.html", pagina(
        cfg, s, corpo, "Órgãos anuentes — Catálogo de Produtos do Portal Único",
        "Índice dos órgãos que exigem atributos no Catálogo de Produtos, "
        "com o número de atributos de cada um.", "/orgaos/",
        trilha(cfg, [("Início", "/"), ("Órgãos", "/orgaos/")]))))
    return caminhos


def gerar_fontes(cfg):
    """Copia as fontes auto-hospedadas para site/.

    Auto-hospedadas de proposito: a pagina de privacidade afirma que o site
    nao faz requisicao a terceiro, e carregar do Google Fonts contradiria
    isso. Sao variaveis - um arquivo por subset serve todos os pesos.
    """
    if not os.path.isdir(DIR_FONTES):
        return
    destino = os.path.join(DIR_SITE, "fontes")
    os.makedirs(destino, exist_ok=True)
    prefixo = cfg.get("base_path", "").rstrip("/")
    for nome in os.listdir(DIR_FONTES):
        origem = os.path.join(DIR_FONTES, nome)
        if nome.endswith(".css"):
            with open(origem, encoding="utf-8") as f:
                css = f.read()
            # O prefixo de base_path nao alcanca arquivo externo ao HTML.
            if prefixo:
                css = css.replace("url(/fontes/", f"url({prefixo}/fontes/")
            with open(os.path.join(destino, nome), "w",
                      encoding="utf-8", newline=chr(10)) as f:
                f.write(css)
        else:
            shutil.copy2(origem, os.path.join(destino, nome))


def gerar_cname(cfg):
    """O Pages exige CNAME na raiz publicada, e o site/ e apagado a cada build."""
    dominio = cfg.get("dominio")
    if dominio:
        escrever("CNAME", dominio + chr(10))


def gerar_indexnow(cfg):
    """Chave do IndexNow: o Bing aceita ping instantaneo de URL alterada."""
    chave = cfg.get("indexnow_key")
    if chave:
        escrever(chave + ".txt", chave)


def gerar_privacidade(cfg, s):
    """Pagina de privacidade.

    O site nao coleta nada no servidor (o mailto abre o cliente do visitante),
    mas roda analitica - e para um publico de compliance a ausencia da pagina
    custa mais credibilidade do que o esforco de escreve-la.
    """
    corpo = template("privacidade.html")
    return [escrever("privacidade/index.html", pagina(
        cfg, s, corpo,
        "Privacidade — Sentinela do Catálogo",
        "O que este site coleta: nada no servidor, analítica sem cookie, e o "
        "que é feito com o e-mail de quem escreve.",
        "/privacidade/"))]


def gerar_feed(cfg, s):
    """RSS das viradas agendadas.

    E a unica forma de push que este teste entrega: quem acompanha comex por
    leitor de feed passa a ser avisado sem precisar visitar a pagina.
    """
    base = (cfg.get("base_url", "").rstrip("/")
            + cfg.get("base_path", "").rstrip("/"))
    pub = formatdate(
        datetime.strptime(s["data_referencia"], "%Y-%m-%d")
        .replace(tzinfo=timezone.utc).timestamp(), usegmt=True)

    itens = []
    for v in s["viradas"]:
        titulo = (f'NCM {v["ncm"]}: {v["nome"] or v["atributo"]} '
                  f'vira obrigatório em {br(v["vira_obrigatorio_em"])}')
        desc = (f'O atributo {v["atributo"]} ({v["nome"] or "sem nome"}), exigido por '
                f'{"/".join(v["orgaos"]) or "órgão não identificado"}, deixa de ser '
                f'opcional em {por_extenso(v["vira_obrigatorio_em"])}. Produtos da NCM '
                f'{v["ncm"]} sem ele preenchido são desativados no Catálogo de Produtos.')
        link = f'{base}/ncm/{v["ncm"]}/'
        guid = f'{link}#{v["atributo"]}-{v["vira_obrigatorio_em"]}'
        itens.append(
            f"<item><title>{esc(titulo)}</title><link>{esc(link)}</link>"
            f"<guid isPermaLink=\"false\">{esc(guid)}</guid>"
            f"<description>{esc(desc)}</description>"
            f"<pubDate>{pub}</pubDate></item>")

    escrever("feed.xml",
             '<?xml version="1.0" encoding="UTF-8"?>'
             '<rss version="2.0"><channel>'
             '<title>Sentinela do Catálogo — viradas de atributo por NCM</title>'
             f'<link>{esc(base)}/</link>'
             '<description>Atributos do Catálogo de Produtos do Portal Único que '
             'têm data marcada para virar obrigatórios.</description>'
             '<language>pt-BR</language>'
             f'<lastBuildDate>{pub}</lastBuildDate>'
             f'{"".join(itens)}'
             '</channel></rss>')


def gerar_sitemap(cfg, caminhos, s):
    base = (cfg.get("base_url", "").rstrip("/")
            + cfg.get("base_path", "").rstrip("/"))
    hoje = s["data_referencia"]
    urls = "".join(
        f"<url><loc>{esc(base + c)}</loc><lastmod>{hoje}</lastmod></url>"
        for c in sorted(set(caminhos)))
    escrever("sitemap.xml",
             '<?xml version="1.0" encoding="UTF-8"?>'
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
             f"{urls}</urlset>")
    escrever("robots.txt",
             f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n")
    escrever(".nojekyll", "")


def main():
    if not os.path.exists(ARQ_ULTIMO):
        print(f"ERRO: {ARQ_ULTIMO} nao existe. Rode coletor.py antes.", file=sys.stderr)
        return 1
    with open(ARQ_ULTIMO, encoding="utf-8") as f:
        s = json.load(f)
    if not os.path.exists(ARQ_ATRIBUTOS):
        print(f"ERRO: {ARQ_ATRIBUTOS} nao existe. Rode coletor.py antes.",
              file=sys.stderr)
        return 1
    with open(ARQ_ATRIBUTOS, encoding="utf-8") as f:
        catalogo = json.load(f)

    cfg = config()
    if os.path.isdir(DIR_SITE):
        shutil.rmtree(DIR_SITE)

    caminhos = []
    caminhos += gerar_index(cfg, s)
    caminhos += gerar_ncms(cfg, s)
    caminhos += gerar_atributos(cfg, s, catalogo)
    caminhos += gerar_orgaos(cfg, s, catalogo)
    caminhos += gerar_privacidade(cfg, s)
    gerar_feed(cfg, s)
    gerar_fontes(cfg)
    gerar_cname(cfg)
    gerar_indexnow(cfg)
    gerar_sitemap(cfg, caminhos, s)

    print(f"{len(caminhos)} paginas geradas em site/ "
          f"(referencia {br(s['data_referencia'])}, {len(s['viradas'])} viradas)")
    if not (cfg.get("form_embed_url") or cfg.get("contato_email")):
        print("AVISO: captura nao configurada - defina contato_email (mailto) ou "
              "form_embed_url em config.json. Sem isso o teste nao produz metrica.")
    if not cfg.get("goatcounter_code"):
        print("AVISO: goatcounter_code vazio - sem analitica, zero cadastro nao "
              "distingue 'ninguem quer' de 'ninguem viu'.")
    if not cfg.get("base_url"):
        print("AVISO: base_url vazio em config.json - "
              "canonical e sitemap saem com caminho relativo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
