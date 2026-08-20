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

MESES = ("", "janeiro", "fevereiro", "março", "abril", "maio", "junho",
         "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")


def config():
    padrao = {"base_url": "", "base_path": "", "form_embed_url": "",
              "contato_email": "", "goatcounter_code": ""}
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


def pagina(cfg, snapshot, corpo, titulo, descricao, caminho):
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
        "analytics": bloco_analytics(cfg),
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
        linhas.append(
            f'<tr>'
            f'<td class="ncm"><a href="/ncm/{v["ncm"]}/">{esc(v["ncm"])}</a></td>'
            f'<td><a href="/atributos/{v["atributo"]}/">{esc(v["nome"] or v["atributo"])}</a>'
            f'<br><span style="font-family:var(--mono);font-size:.8rem;color:var(--faint)">'
            f'{esc(v["atributo"])}</span></td>'
            f'<td>{esc("/".join(v["orgaos"]) or "—")}</td>'
            f'<td class="data">{br(v["vira_obrigatorio_em"])}'
            f'<br><span style="font-size:.8rem;color:var(--faint)">{prazo}</span></td>'
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


def bloco_historico():
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
            f'<li><a href="/ncm/{v["ncm"]}/">{esc(v["ncm"])}</a> — '
            f'{esc(v["nome"] or v["atributo"])}, a partir de {br(v["vira_obrigatorio_em"])}</li>'
            for v in sorted(novas, key=lambda x: x["vira_obrigatorio_em"]))
        partes.append(f"<h3>Viradas novas</h3><ul>{itens}</ul>")
    if sumiram:
        itens = "".join(
            f'<li><a href="/ncm/{n}/">{esc(n)}</a> — {esc(c)}</li>'
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
        h1 = (f"{len(vs)} atributos de NCM viram obrigatórios "
              f"nos próximos {max(dias, 1)} dias")
        lede = (f"São {len(vs)} vínculos em {ncms} NCMs. "
                f"Os produtos dessas NCMs que estiverem sem o atributo preenchido "
                f"na data são desativados no Catálogo de Produtos do Portal Único.")
        aviso = (f'<div class="aviso"><strong>Próximo corte: {por_extenso(proxima)}</strong>'
                 f'{" — é hoje." if dias == 0 else f" — faltam {dias} dias."} '
                 f'Exigência {"do " + orgaos[0] if len(orgaos) == 1 else "dos órgãos anuentes"}.'
                 f'</div>')
        descricao = (f"Lista atualizada das {len(vs)} NCMs com atributos que viram "
                     f"obrigatórios no Catálogo de Produtos do Portal Único. "
                     f"Próximo corte em {br(proxima)}.")
    else:
        h1 = "Nenhum atributo de NCM tem virada agendada hoje"
        lede = ("O arquivo oficial de hoje não traz nenhum vínculo com data para virar "
                "obrigatório. Esta página é atualizada todo dia — quando a Receita "
                "agendar uma nova virada, ela aparece aqui.")
        aviso = ""
        descricao = ("Monitoramento diário dos atributos de NCM que viram obrigatórios "
                     "no Catálogo de Produtos do Portal Único.")

    corpo = preencher(template("index.html"), {
        "data_ref": br(s["data_referencia"]),
        "h1": esc(h1),
        "lede": esc(lede),
        "aviso": aviso,
        "tabela": tabela_viradas(vs, ref),
        "historico": bloco_historico(),
    })
    titulo = ("Atributos de NCM que viram obrigatórios — Catálogo do Portal Único"
              if vs else "Atributos de NCM com virada agendada — Portal Único")
    caminhos.append(escrever("index.html", pagina(cfg, s, corpo, titulo, descricao, "/")))
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
        aviso = (f'<div class="aviso"><strong>Faltam {dias} dias.</strong> '
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
        caminhos.append(escrever(f"ncm/{ncm}/index.html",
                                 pagina(cfg, s, corpo, titulo, descricao, f"/ncm/{ncm}/")))

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


def gerar_atributos(cfg, s):
    caminhos = []
    virando = {}
    for v in s["viradas"]:
        virando.setdefault(v["atributo"], []).append(v)

    for a in s["atributos_destaque"]:
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

        orgaos = "/".join(a.get("orgaos") or []) or "—"
        forma = {"LISTA_ESTATICA": "lista de opções", "BOOLEANO": "sim ou não",
                 "TEXTO": "texto livre", "NUMERO_REAL": "número decimal",
                 "NUMERO_INTEIRO": "número inteiro", "DATA": "data"
                 }.get(a.get("forma_preenchimento"), a.get("forma_preenchimento") or "—")
        lede = (f"Atributo exigido por {orgaos}, preenchido como {forma}. "
                f"Aplica-se a {milhar(a['total_ncms'])} NCM"
                f"{'s' if a['total_ncms'] != 1 else ''}.")

        definicao = (f"<h2>O que é</h2><p>{esc(a['definicao'])}</p>"
                     if a.get("definicao") else "")
        orientacao = (f"<h2>Orientação oficial de preenchimento</h2>"
                      f"<p>{esc(a['orientacao'])}</p>" if a.get("orientacao") else "")

        if a.get("dominio"):
            opcoes = "".join(f"<dt>{esc(d['codigo'])}</dt><dd>{esc(d['descricao'])}</dd>"
                             for d in a["dominio"])
            dominio = (f"<h2>Opções válidas ({len(a['dominio'])})</h2>"
                       f'<dl class="dominio">{opcoes}</dl>')
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

        corpo = preencher(template("atributo.html"), {
            "codigo": esc(cod), "h1": esc(h1), "lede": esc(lede), "aviso": aviso,
            "definicao": definicao, "orientacao": orientacao, "dominio": dominio,
            "aplicacao": esc(aplicacao),
            "ncms": f'<ul class="limpa">{chips}</ul>' if chips else "",
        })
        titulo = f"{nome} ({cod}) — Catálogo de Produtos do Portal Único"
        caminhos.append(escrever(f"atributos/{cod}/index.html",
                                 pagina(cfg, s, corpo, titulo, descricao,
                                        f"/atributos/{cod}/")))

    itens = "".join(
        f'<li><a href="/atributos/{a["codigo"]}/">{esc(a["codigo"])} · '
        f'{esc(a.get("nome") or "")}</a></li>' for a in s["atributos_destaque"])
    corpo = (f'<span class="chapeu">Índice</span><h1>Atributos do Catálogo de Produtos</h1>'
             f'<p class="lede">O que cada atributo exige, as opções válidas e em quais '
             f'NCMs se aplica. Começando pelos que têm virada agendada e pelos mais '
             f'usados.</p><ul class="limpa" style="flex-direction:column">{itens}</ul>')
    caminhos.append(escrever("atributos/index.html", pagina(
        cfg, s, corpo, "Atributos do Catálogo de Produtos — Portal Único",
        "Índice dos atributos do Catálogo de Produtos do Portal Único, com opções "
        "válidas e NCMs onde se aplicam.", "/atributos/")))
    return caminhos


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

    cfg = config()
    if os.path.isdir(DIR_SITE):
        shutil.rmtree(DIR_SITE)

    caminhos = []
    caminhos += gerar_index(cfg, s)
    caminhos += gerar_ncms(cfg, s)
    caminhos += gerar_atributos(cfg, s)
    gerar_feed(cfg, s)
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
