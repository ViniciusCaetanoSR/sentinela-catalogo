"""Gera o site inteiro a partir da fixture e confere que ele fecha.

E o teste de maior valor da suite: teria pego os 17 links quebrados que
ficaram meses no ar, e e o que segura o corte das paginas quase-duplicadas -
cortar um atributo sem parar de linkar para ele quebraria o site em silencio.
"""

import contextlib
import csv
import html.parser
import io
import json
import os
import re
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apoio  # noqa: E402
import comum  # noqa: E402
import gerar_site as g  # noqa: E402

HOJE = date(2026, 8, 22)

# action entra porque o form "Ir para a NCM" aponta para /ncm/ com o prefixo,
# e sem JS é para lá que o envio vai.
RE_LINK = re.compile(r'(?:href|src|action)="([^"]+)"')
EXTERNO = ("http://", "https://", "mailto:", "//", "#", "data:")
NS_SITEMAP = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _chaves_dos_templates():
    """As {{chaves}} que existem nos templates reais, extraídas por regex.

    É contra ESTAS que a saída é conferida - não contra qualquer "{{": um
    "{{" que venha do arquivo oficial (ATT_HOSTIL carrega um na
    orientação) é dado, e tem de sair intacto, escapado como texto.
    """
    chaves = set()
    pasta = comum.padrao().templates
    for nome in os.listdir(pasta):
        if nome.endswith(".html"):
            with open(os.path.join(pasta, nome), encoding="utf-8") as f:
                chaves |= set(g.RE_CHAVE.findall(f.read()))
    return chaves


CHAVES_TEMPLATES = _chaves_dos_templates()
# O único "{{chave}}" que a fixture carrega de propósito, e onde ele pousa:
# a orientação de ATT_HOSTIL diz "{{formulario}}", e ela só é mostrada na
# página do próprio atributo. Em qualquer outra página, ou mais de uma vez
# nesta, é placeholder que ficou sem preencher.
CHAVES_TOLERADAS = {("/atributos/ATT_HOSTIL/", "formulario"): 1}


def setUpModule():
    apoio.proibir_rede()


def _monta(destino, base_path, referencia=HOJE, ajustar=None, dados=None, cfg=None):
    """Roda coletor (sem rede) e gerador contra um diretorio temporario.

    Os dados saem de apurar() + gravar(), o mesmo caminho da producao; o
    gerador roda pela main(), com --raiz, como o workflow o chama.
    ajustar(snapshot, catalogo, completo) roda antes da gravacao, para os
    testes que precisam de um dado deliberadamente errado; `dados` troca a
    amostra por outro JSON (a fixture do modo lote, montada em memoria);
    `cfg` acrescenta chaves ao config.json do ambiente.
    """
    with apoio.ambiente(destino, dict(cfg or {}, base_path=base_path)) as caminhos:
        apuracao = apoio.montar_dados(
            caminhos, dados if dados is not None else apoio.amostra(), referencia, ajustar
        )
        # O gerador fala bastante; nos testes o que importa e o resultado.
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            codigo = g.main(["--raiz", destino])
    return codigo, caminhos.site, apuracao.com_pagina


def _le(*partes):
    with open(os.path.join(*partes), encoding="utf-8") as f:
        return f.read()


def _lastmod(destino):
    return json.loads(_le(destino, "dados", "lastmod.json"))


def _paginas_do_lastmod(lastmod):
    """[(caminho, [hash, data])] sem a chave de metadado dos templates."""
    return [(c, r) for c, r in lastmod.items() if not c.startswith("__")]


def _tbody(html):
    """So as linhas da tabela: a legenda da pagina tambem usa as etiquetas."""
    return html.split("<tbody")[1].split("</tbody>")[0]


def _arquivos(raiz):
    """Todo caminho servivel, incluindo a forma sem index.html."""
    existe = set()
    for base, _, nomes in os.walk(raiz):
        for nome in nomes:
            rel = "/" + os.path.relpath(os.path.join(base, nome), raiz).replace(os.sep, "/")
            existe.add(rel)
            if rel.endswith("/index.html"):
                existe.add(rel[: -len("index.html")])
    return existe


class TestSiteFecha(unittest.TestCase):
    def _roda_em(self, base_path="/repo"):
        """Gera o site inteiro numa raiz temporaria: (raiz, site, com_pagina).

        A raiz interessa a quem confere dados/lastmod.json, que fica ao LADO
        de site/ e nao dentro dele.
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        codigo, site, com_pagina = _monta(tmp.name, base_path)
        self.assertEqual(codigo, 0)
        return tmp.name, site, com_pagina

    def _roda(self, base_path):
        """O caso comum: quem nao precisa da raiz so quer estes dois."""
        _, site, com_pagina = self._roda_em(base_path)
        return site, com_pagina

    def test_com_base_path(self):
        self._confere("/repo")

    def test_sem_base_path(self):
        self._confere("")

    def _confere(self, base_path):
        site, _ = self._roda(base_path)
        self._confere_fechado(site, base_path)

    def _confere_fechado(self, site, base_path):
        existe = _arquivos(site)
        paginas = [
            os.path.join(b, n)
            for b, _, ns in os.walk(site)
            for n in ns
            if n.endswith(".html")
        ]
        self.assertTrue(paginas)

        quebrados, sem_prefixo, placeholders = [], [], []
        for caminho in paginas:
            with open(caminho, encoding="utf-8") as f:
                html = f.read()
            publicado = g.caminho_publicado(os.path.relpath(caminho, site))
            # Só as chaves que os templates têm: um "{{" vindo do dado
            # oficial não é erro. E a contagem é exata - a orientação de
            # ATT_HOSTIL carrega um "{{formulario}}" que tem de aparecer
            # UMA vez na página dele, e nenhuma nas outras.
            for chave in CHAVES_TEMPLATES:
                marcador = "{{" + chave + "}}"
                esperado = CHAVES_TOLERADAS.get((publicado, chave), 0)
                if html.count(marcador) != esperado:
                    placeholders.append((caminho, marcador))
            for alvo in RE_LINK.findall(html):
                if alvo.startswith(EXTERNO):
                    continue
                if base_path and not alvo.startswith(base_path):
                    sem_prefixo.append((caminho, alvo))
                    continue
                # O fragmento e a query saem nos dois casos: sem base_path um
                # "/x/#y" também precisa resolver para "/x/". A query aparece
                # no action do formulário de busca de NCM, que cai em /ncm/.
                local = alvo[len(base_path) :] if base_path else alvo
                local = local.split("#")[0].split("?")[0]
                if local not in existe:
                    quebrados.append((caminho, alvo))

        self.assertEqual(placeholders, [], "sobrou {{chave}} na saida")
        self.assertEqual(sem_prefixo, [], "link interno sem o prefixo do base_path")
        self.assertEqual(quebrados, [], "link interno para arquivo inexistente")

    def test_nao_linka_atributo_sem_pagina(self):
        # O corte das quase-duplicatas so e seguro por causa disto.
        site, com_pagina = self._roda("/repo")
        alvos = set()
        for base, _, nomes in os.walk(site):
            for nome in nomes:
                if not nome.endswith(".html"):
                    continue
                with open(os.path.join(base, nome), encoding="utf-8") as f:
                    for alvo in RE_LINK.findall(f.read()):
                        m = re.search(r"/atributos/([^/]+)/", alvo)
                        if m:
                            alvos.add(m.group(1))
        self.assertTrue(
            alvos <= com_pagina, f"linka para atributo sem pagina: {alvos - com_pagina}"
        )

    def test_conteudo_do_atributo_cortado_aparece_na_ncm(self):
        # ATT_CLONE_A perdeu a pagina propria; as opcoes dele tem de estar
        # visiveis na pagina da NCM 8609.00.00.
        site, _ = self._roda("/repo")
        with open(
            os.path.join(site, "ncm", "8609.00.00", "index.html"), encoding="utf-8"
        ) as f:
            html = f.read()
        self.assertIn("Escolher apenas um Destaque", html)
        self.assertIn("EXCETO DE ESP", html)

    def test_404_fora_do_sitemap(self):
        site, _ = self._roda("/repo")
        self.assertTrue(os.path.exists(os.path.join(site, "404.html")))
        for nome in os.listdir(site):
            if nome.startswith("sitemap") and nome.endswith(".xml"):
                self.assertNotIn("/404", _le(site, nome))

    def test_404_tem_noindex_e_sem_canonical(self):
        # Pagina de erro servida em qualquer endereco ausente: um canonical
        # fixo convidaria o Google a indexa-la como conteudo.
        site, _ = self._roda("/repo")
        html = _le(site, "404.html")
        self.assertIn('<meta name="robots" content="noindex">', html)
        self.assertNotIn('rel="canonical"', html)
        self.assertNotIn('property="og:url"', html)
        self.assertNotIn("{{", html)
        # E as demais paginas continuam com os dois.
        home = _le(site, "index.html")
        self.assertIn('<link rel="canonical" href="https://exemplo.test/repo/">', home)
        self.assertIn('<meta property="og:url" content="https://exemplo.test/repo/">', home)
        self.assertNotIn("noindex", home)

    def test_status_json_tem_as_chaves(self):
        # O que a conferencia pos-deploy le da URL publica para saber se o
        # que esta no ar e o build desta run, e nao um artifact velho.
        raiz, site, _ = self._roda_em()
        status = json.loads(_le(site, "status.json"))
        self.assertEqual(status["data_referencia"], HOJE.isoformat())
        self.assertEqual(status["schema"], 2)
        self.assertGreater(status["viradas"], 0)
        self.assertRegex(status["proximo_corte"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertTrue(status["gerado_em"].endswith("+00:00"))
        # O piso de paginas do workflow so vale se este numero for o mesmo
        # que o sitemap e o lastmod contam.
        self.assertEqual(status["paginas"], len(_paginas_do_lastmod(_lastmod(raiz))))

    def test_status_fora_do_sitemap_e_do_lastmod(self):
        # "gerado_em" muda a cada build: dentro do lastmod ele carimbaria
        # data nova todo dia e mandaria a URL ao IndexNow a toa. E um JSON
        # de saude nao e conteudo para indexar.
        raiz, site, _ = self._roda_em()
        self.assertNotIn("/status.json", _lastmod(raiz))
        for nome in os.listdir(site):
            if nome.startswith("sitemap") and nome.endswith(".xml"):
                self.assertNotIn("status.json", _le(site, nome), nome)
        self.assertNotIn("status.json", _le(site, "mudancas.txt"))

    def test_paginas_com_virada_nao_mudam_de_hash_entre_dias(self):
        # Mesmas viradas, outro dia de referencia: "Faltam N dias" muda no
        # HTML, mas o lastmod da home e da NCM com virada tem de ficar.
        # (Dia 21, não 23: no 23 ATT_HOJE já venceu e o conteúdo muda de
        # verdade - esse caso é o de test_lastmod_carimba_so_o_que_mudou.)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        _monta(tmp.name, "/repo", referencia=date(2026, 8, 22))
        primeiro = _lastmod(tmp.name)
        home_antes = _le(tmp.name, "site", "index.html")
        _monta(tmp.name, "/repo", referencia=date(2026, 8, 21))
        segundo = _lastmod(tmp.name)
        home_depois = _le(tmp.name, "site", "index.html")
        self.assertNotEqual(home_antes, home_depois, "o HTML deveria ter mudado")
        for caminho in (
            "/",
            "/ncm/8415.10.90/",
            "/ncm/8418.69.20/",
            "/atributos/ATT_FUTURO/",
            "/atributos/ATT_HOSTIL/",
        ):
            self.assertIn(caminho, primeiro)
            self.assertEqual(primeiro[caminho], segundo[caminho], caminho)
        self.assertEqual(primeiro, segundo)
        self.assertEqual(_le(tmp.name, "site", "mudancas.txt"), "")

    # O que muda de um dia para o outro SEM mudança de dado: ATT_HOJE vira
    # em 22/08 e no dia 23 é prazo vencido. Isso toca a NCM dele, a página
    # dele (perde o aviso de virada), o índice de atributos e a página do
    # INMETRO (ele sai de "com virada agendada"), o capítulo 84 (a marca
    # "virada" da NCM some) e a home (a tabela e o "saiu da lista").
    VENCIMENTO_DE_ATT_HOJE = {
        "/",
        "/ncm/8418.69.20/",
        "/ncm/capitulo-84/",
        "/atributos/",
        "/atributos/ATT_HOJE/",
        "/orgaos/inmetro/",
    }

    def test_lastmod_carimba_so_o_que_mudou(self):
        # Dia 22; dia 23 com o mesmo dado (só o calendário andou); dia 24 com
        # a data de ATT_FUTURO adiada na fixture em memória. Cada passo
        # carimba data nova SÓ nas páginas cujo dado mudou, e mudancas.txt
        # lista exatamente elas.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        _monta(tmp.name, "/repo", referencia=date(2026, 8, 22))
        dia22 = _lastmod(tmp.name)

        _monta(tmp.name, "/repo", referencia=date(2026, 8, 23))
        dia23 = _lastmod(tmp.name)
        mudadas = {c for c, (_, d) in _paginas_do_lastmod(dia23) if d == "2026-08-23"}
        self.assertEqual(mudadas, self.VENCIMENTO_DE_ATT_HOJE)
        self.assertEqual(
            set(_le(tmp.name, "site", "mudancas.txt").splitlines()),
            {"https://exemplo.test/repo" + c for c in self.VENCIMENTO_DE_ATT_HOJE},
        )

        dados = apoio.amostra()
        for ncm in dados["listaNcm"]:
            for v in ncm.get("listaAtributos") or []:
                if v.get("codigo") == "ATT_FUTURO":
                    v["dataFimVigencia"] = "2099-06-30"
        _monta(tmp.name, "/repo", referencia=date(2026, 8, 24), dados=dados)
        dia24 = _lastmod(tmp.name)
        afetadas = {"/", "/ncm/8415.10.90/", "/atributos/ATT_FUTURO/"}
        for caminho, (marca, data) in _paginas_do_lastmod(dia24):
            if caminho in afetadas:
                self.assertEqual(data, "2026-08-24", caminho)
                self.assertNotEqual(marca, dia23[caminho][0], caminho)
            else:
                self.assertEqual(dia24[caminho], dia23[caminho], caminho)
        # O conjunto de páginas é o mesmo: nada entrou nem saiu.
        self.assertEqual(set(dia24), set(dia23))
        self.assertEqual(
            sorted(_le(tmp.name, "site", "mudancas.txt").splitlines()),
            sorted("https://exemplo.test/repo" + c for c in afetadas),
        )
        # E a página intocada continua com a data do primeiro dia.
        self.assertEqual(dia24["/ncm/8703.10.00/"][1], "2026-08-22")
        self.assertEqual(dia24["/ncm/8703.10.00/"], dia22["/ncm/8703.10.00/"])
        self.assertEqual(dia24["/privacidade/"], dia22["/privacidade/"])
        self.assertIn("de 31/12/2099 para 30/06/2099", _le(tmp.name, "site", "index.html"))

    def test_js_nao_e_alterado(self):
        # A remocao de comentarios nao entende string nem regex de JS: o
        # app.js e servido como esta no template.
        site, _ = self._roda("/repo")
        servidos = [
            n for n in os.listdir(site) if n.startswith("app.") and n.endswith(".js")
        ]
        self.assertEqual(len(servidos), 1)
        self.assertEqual(_le(site, servidos[0]), _le(comum.padrao().templates, "app.js"))
        folhas = [n for n in os.listdir(site) if n.startswith("estilo.")]
        self.assertEqual(len(folhas), 1)
        self.assertNotIn("/*", _le(site, folhas[0]))

    def test_favicons_existem(self):
        site, _ = self._roda("/repo")
        for nome, lado in (("favicon-32.png", 32), ("apple-touch-icon.png", 180)):
            with open(os.path.join(site, nome), "rb") as f:
                cabeca = f.read(24)
            self.assertEqual(cabeca[:8], b"\x89PNG\r\n\x1a\n", nome)
            self.assertEqual(struct.unpack(">II", cabeca[16:24]), (lado, lado), nome)
        home = _le(site, "index.html")
        self.assertIn(
            '<link rel="icon" href="/repo/favicon.svg" type="image/svg+xml" sizes="any">',
            home,
        )
        self.assertIn('href="/repo/favicon-32.png"', home)
        self.assertIn(
            '<link rel="apple-touch-icon" href="/repo/apple-touch-icon.png">', home
        )
        self.assertIn('<meta name="theme-color" content="#1e1e1e">', home)
        # charset antes de qualquer byte que nao seja ASCII no head.
        self.assertLess(home.index('<meta charset="utf-8">'), home.index("<script"))

    def test_versao_divergente_aborta_antes_de_apagar_o_site(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        site = os.path.join(tmp.name, "site")
        os.makedirs(site)
        marca = os.path.join(site, "de-ontem.html")
        with open(marca, "w", encoding="utf-8") as f:
            f.write("site de ontem")

        def envelhece(snapshot, catalogo, completo):
            completo["versao"] = "998"

        codigo, _, _ = _monta(tmp.name, "/repo", ajustar=envelhece)
        self.assertEqual(codigo, 1)
        self.assertTrue(os.path.exists(marca), "site/ foi apagado apesar do erro")
        self.assertFalse(os.path.exists(os.path.join(site, "index.html")))

    def test_situacao_vazia_nao_tem_rotulo(self):
        site, _ = self._roda("/repo")
        raiz = os.path.join(site, "orgaos")
        paginas = [
            os.path.join(b, n)
            for b, _, ns in os.walk(raiz)
            for n in ns
            if n.endswith(".html")
        ]
        self.assertTrue(paginas)
        com_marca = sem_marca = 0
        for caminho in paginas:
            html = _le(caminho)
            self.assertNotIn('data-rot="Situação"></td>', html)
            com_marca += html.count(
                '<td role="cell" data-rot="Situação"><span class="tag muda">'
            )
            sem_marca += html.count('<td role="cell"></td>')
        self.assertTrue(com_marca, "nenhum atributo com virada na pagina de orgao")
        self.assertTrue(sem_marca, "nenhuma celula vazia sem rotulo")

    def test_contagem_da_home_fala_com_o_leitor_de_tela(self):
        site, _ = self._roda("/repo")
        home = _le(site, "index.html")
        self.assertIn(
            '<p class="contagem-topo"><span class="oculto">'
            "O próximo corte é hoje, 22/08/2026.</span>",
            home,
        )
        self.assertNotIn('aria-label="É hoje', home)
        self.assertIn('<section class="captura" aria-labelledby="captura-titulo">', home)
        self.assertIn('<h2 id="captura-titulo">', home)

    def test_paginas_carregam_data_referencia(self):
        # É o que o app.js lê para avisar que o dado envelheceu: tem de
        # estar em TODA página, inclusive na de erro.
        site, _ = self._roda("/repo")
        for base, _, nomes in os.walk(site):
            for nome in nomes:
                if nome.endswith(".html"):
                    self.assertIn(
                        '<body data-referencia="2026-08-22">',
                        _le(base, nome),
                        os.path.join(base, nome),
                    )

    def test_home_tem_data_corte(self):
        # O cartão, as células de prazo da tabela e os avisos de NCM e de
        # atributo carregam a data do corte: o app.js refaz o prazo com o
        # relógio do navegador, e o texto do build fica para quem não roda JS.
        site, _ = self._roda("/repo")
        home = _le(site, "index.html")
        self.assertIn(
            "<h1>3 atributos de NCM viram obrigatórios "
            '<span data-corte="2026-08-22" data-estilo="h1">hoje</span></h1>',
            home,
        )
        self.assertIn(
            '<span class="contagem-num" data-contagem="0" data-corte="2026-08-22" '
            'aria-hidden="true" style="--digitos:1">0</span>'
            '<span class="contagem-un" aria-hidden="true">dias</span>',
            home,
        )
        self.assertIn(
            '<span class="prazo-txt urgente" data-corte="2026-08-22">hoje</span>'
            '<span class="prazo urgente"><i style="--w:6%"></i></span>',
            home,
        )
        self.assertIn('data-corte="2099-12-31">em 26794 dias</span>', home)
        ncm = _le(site, "ncm", "8418.69.20", "index.html")
        self.assertIn(
            '<div class="aviso"><strong data-corte="2026-08-22">É hoje.</strong> '
            "Exigência do INMETRO.",
            ncm,
        )
        atributo = _le(site, "atributos", "ATT_FUTURO", "index.html")
        self.assertIn(
            '<div class="aviso"><strong data-corte="2099-12-31">Faltam 26794 dias.'
            '</strong> Este atributo vira obrigatório em <time datetime="2099-12-31">'
            "31/12/2099</time> para 1 NCM:",
            atributo,
        )

    def test_busca_de_ncm_na_home_no_indice_e_no_404(self):
        site, _ = self._roda("/repo")
        inicio = '<form class="ir-ncm" action="/repo/ncm/" method="get" role="search"'
        for partes, marca in (
            (("index.html",), ">"),
            (("ncm", "index.html"), ">"),
            (("404.html",), ' data-404="1">'),
        ):
            html = _le(site, *partes)
            self.assertEqual(html.count(inicio + marca), 1, partes)
            self.assertEqual(html.count("<form"), 1, partes)
            self.assertIn('<label for="ir-ncm-campo">Ir para a NCM</label>', html)
        # Fora das três, nenhuma: uma NCM não precisa de busca de NCM.
        self.assertNotIn("ir-ncm", _le(site, "ncm", "8415.10.90", "index.html"))
        self.assertNotIn("ir-ncm", _le(site, "atributos", "index.html"))

    def test_ncm_sem_pontos_no_chapeu_e_na_description(self):
        site, _ = self._roda("/repo")
        for ncm, digitos in (("8415.10.90", "84151090"), ("8703.10.00", "87031000")):
            html = _le(site, "ncm", ncm, "index.html")
            self.assertIn(
                f'<span class="chapeu">NCM {ncm} <button class="copiar" type="button" '
                f'data-copiar="{ncm}"',
                html,
            )
            self.assertIn(
                f'· <span class="ncm-digitos">{digitos}</span> <button class="copiar" '
                f'type="button" data-copiar="{digitos}"',
                html,
            )
            self.assertRegex(
                html,
                '<meta name="description" content="[^"]*NCM '
                + re.escape(f"{ncm} ({digitos})"),
            )
            # Um único status para os dois botões.
            self.assertEqual(html.count('id="copia-status"'), 1)

    def test_paginacao_e_nav_com_aria_current(self):
        antigo = g.POR_PAGINA
        g.POR_PAGINA = 2
        self.addCleanup(setattr, g, "POR_PAGINA", antigo)
        site, _ = self._roda("/repo")
        html = _le(site, "ncm", "capitulo-84", "index.html")
        self.assertIn('<nav class="paginacao" aria-label="Páginas do capítulo">', html)
        self.assertIn('<a href="/repo/ncm/capitulo-84/" aria-current="page">1</a>', html)
        self.assertIn('<a href="/repo/ncm/capitulo-84/pagina-2/">2</a>', html)
        self.assertIn("página 1 de 2", html)
        self.assertIn("(8415.10.90 a 8418.69.20)", html)
        self._confere_fechado(site, "/repo")

    def test_geracao_e_deterministica(self):
        tmp1 = tempfile.TemporaryDirectory()
        tmp2 = tempfile.TemporaryDirectory()
        self.addCleanup(tmp1.cleanup)
        self.addCleanup(tmp2.cleanup)
        _, a, _ = _monta(tmp1.name, "/repo")
        _, b, _ = _monta(tmp2.name, "/repo")
        for base, _, nomes in os.walk(a):
            for nome in nomes:
                x = os.path.join(base, nome)
                y = os.path.join(b, os.path.relpath(x, a))
                self.assertTrue(os.path.exists(y), f"faltou {y}")
                # status.json e o unico arquivo deliberadamente volatil: ele
                # existe para provar QUANDO o build rodou, e por isso carrega
                # "gerado_em". Fica fora do sitemap e do lastmod, entao a
                # volatilidade nao contamina nada indexavel - e site/ nao e
                # versionado, entao tambem nao vira churn de git.
                if nome == "status.json":
                    continue
                with open(x, "rb") as f1, open(y, "rb") as f2:
                    self.assertEqual(f1.read(), f2.read(), f"difere: {nome}")

    def test_todo_ncm_do_mapa_tem_pagina(self):
        site, _ = self._roda("/repo")
        # A NCM sem atributo nenhum e pulada de proposito.
        self.assertFalse(
            os.path.exists(os.path.join(site, "ncm", "9999.99.99", "index.html"))
        )
        self.assertTrue(
            os.path.exists(os.path.join(site, "ncm", "8703.10.00", "index.html"))
        )

    def test_lastmod_so_muda_quando_o_conteudo_muda(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        _monta(tmp.name, "/repo")
        primeiro = _lastmod(tmp.name)
        _monta(tmp.name, "/repo")
        self.assertEqual(primeiro, _lastmod(tmp.name))

    def test_pagina_de_atributo_nao_vira_404_depois_do_corte(self):
        # Dia 22: ATT_CLONE_A vira hoje e ganha página - só por isso: vale
        # para uma NCM e a prosa é boilerplate. Dia 23: a virada passou. A
        # página fica - o sitemap a anunciou - e o site continua fechado.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dados = apoio.amostra()
        for ncm in dados["listaNcm"]:
            for v in ncm.get("listaAtributos") or []:
                if v.get("codigo") == "ATT_CLONE_A":
                    v["dataFimVigencia"] = "2026-08-22"
        _, site, com_pagina_antes = _monta(
            tmp.name, "/repo", referencia=date(2026, 8, 22), dados=dados
        )
        self.assertIn("ATT_CLONE_A", com_pagina_antes)
        _, site, com_pagina_depois = _monta(
            tmp.name, "/repo", referencia=date(2026, 8, 23), dados=dados
        )
        self.assertTrue(com_pagina_antes <= com_pagina_depois)
        self.assertTrue(
            os.path.exists(os.path.join(site, "atributos", "ATT_CLONE_A", "index.html"))
        )
        catalogo = json.loads(_le(tmp.name, "dados", "atributos.json"))
        self.assertEqual(catalogo["paginas_permanentes"], sorted(com_pagina_depois))
        self._confere_fechado(site, "/repo")

    def test_permanente_sem_vinculo_rende_pagina_e_o_site_fecha(self):
        # ATT_ORFAO existe em detalhesAtributos sem nenhuma NCM. Herdado como
        # permanente, ganha página com zero NCMs - sem "vinculado a 0 NCMs:"
        # seguido de nada - e nenhum link do site aponta para o vazio.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        caminhos = comum.Caminhos(raiz=tmp.name)
        os.makedirs(caminhos.dados, exist_ok=True)
        with open(caminhos.atributos, "w", encoding="utf-8") as f:
            json.dump({"paginas_permanentes": ["ATT_ORFAO"]}, f)
        _, site, com_pagina = _monta(tmp.name, "/repo")
        self.assertIn("ATT_ORFAO", com_pagina)
        html = _le(site, "atributos", "ATT_ORFAO", "index.html")
        self.assertIn("não está vinculado a nenhuma NCM no momento", html)
        self.assertNotIn("0 NCMs:", html)
        self._confere_fechado(site, "/repo")

    def test_prazo_alterado_aparece_na_home(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        _monta(tmp.name, "/repo", referencia=date(2026, 8, 22))
        dados = apoio.amostra()
        for ncm in dados["listaNcm"]:
            for v in ncm.get("listaAtributos") or []:
                if v.get("codigo") == "ATT_FUTURO":
                    v["dataFimVigencia"] = "2099-06-30"
        _, site, _ = _monta(tmp.name, "/repo", referencia=date(2026, 8, 23), dados=dados)
        home = _le(site, "index.html")
        self.assertIn("<h3>Prazos alterados</h3>", home)
        self.assertIn(
            '<li><a href="/repo/ncm/8415.10.90/">8415.10.90</a> — Referência de '
            "licenciamento: de 31/12/2099 para 30/06/2099</li>",
            home,
        )
        # Nem nova nem sumida: só mudou de data.
        self.assertNotIn("<h3>Viradas novas</h3>", home)
        self.assertNotIn("Referência de licenciamento, a partir de", home)
        self._confere_fechado(site, "/repo")

    def test_prazo_vencido_aparece_na_ncm(self):
        # ATT_HOJE vira em 22/08. No dia 23 a Receita deveria ter trocado
        # obrigatorio para true; a fixture mantem false - e a pagina diz
        # "prazo vencido" em vez de "opcional". A hipotese central do
        # produto nunca foi verificada: se falhar, o site diz a verdade.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        _monta(tmp.name, "/repo", referencia=date(2026, 8, 22))
        antes = _lastmod(tmp.name)
        tabela = _tbody(_le(tmp.name, "site", "ncm", "8418.69.20", "index.html"))
        self.assertIn(
            '<span class="tag muda">vira obrigatório em '
            '<time datetime="2026-08-22">22/08/2026</time></span>',
            tabela,
        )
        self.assertNotIn("prazo vencido", tabela)

        _monta(tmp.name, "/repo", referencia=date(2026, 8, 23))
        depois = _lastmod(tmp.name)
        tabela = _tbody(_le(tmp.name, "site", "ncm", "8418.69.20", "index.html"))
        self.assertIn(
            '<span class="tag venc">prazo vencido em '
            '<time datetime="2026-08-22">22/08/2026</time></span>',
            tabela,
        )
        self.assertNotIn("vira obrigatório em", tabela)
        self.assertNotIn('<span class="tag opc">', tabela)
        # A situacao mudou de verdade: o lastmod acompanha.
        self.assertNotEqual(antes["/ncm/8418.69.20/"], depois["/ncm/8418.69.20/"])
        # ATT_PASSADO (2020) esta vencido nos dois dias, e o obrigatorio com
        # data (ATT_OBRIGATORIO_FUTURO) nunca e "vencido".
        self.assertEqual(antes["/ncm/8436.21.00/"], depois["/ncm/8436.21.00/"])
        self.assertIn(
            'prazo vencido em <time datetime="2020-01-01">01/01/2020</time>',
            _tbody(_le(tmp.name, "site", "ncm", "8436.21.00", "index.html")),
        )
        self.assertNotIn(
            "prazo vencido",
            _tbody(_le(tmp.name, "site", "ncm", "8504.21.00", "index.html")),
        )
        self._confere_fechado(os.path.join(tmp.name, "site"), "/repo")

    def _locs(self, site, nome):
        """As URLs de um sitemap (índice ou bloco), já sem base_url+base_path."""
        raiz = ET.fromstring(_le(site, nome))
        locs = [
            e.text for e in raiz.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
        ]
        self.assertTrue(locs, nome)
        for loc in locs:
            self.assertTrue(loc.startswith("https://exemplo.test/repo/"), loc)
        for quando in raiz.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod"):
            self.assertRegex(quando.text, r"^\d{4}-\d{2}-\d{2}$")
        return [loc[len("https://exemplo.test/repo") :] for loc in locs]

    def test_paginacao_e_sitemaps_divididos_fecham(self):
        # Com 400 NCMs por página e 5 mil URLs por sitemap a fixture nunca
        # divide nada. Aqui 2 e 3: o capítulo 84 (3 NCMs) pagina, os 10
        # NCMs viram 4 blocos de sitemap, e o site tem de continuar fechado
        # - toda <loc> do índice e dos blocos existe em disco.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with (
            mock.patch.object(g, "POR_PAGINA", 2),
            mock.patch.object(g, "POR_SITEMAP", 3),
        ):
            codigo, site, _ = _monta(tmp.name, "/repo")
        self.assertEqual(codigo, 0)
        self._confere_fechado(site, "/repo")
        existe = _arquivos(site)

        self.assertTrue(
            os.path.exists(os.path.join(site, "ncm", "capitulo-84", "pagina-2"))
        )
        self.assertIn(
            '<a href="/repo/ncm/capitulo-84/pagina-2/">2</a>',
            _le(site, "ncm", "capitulo-84", "index.html"),
        )

        blocos = self._locs(site, "sitemap.xml")
        self.assertGreaterEqual(sum(1 for b in blocos if b.startswith("/sitemap-ncm-")), 2)
        urls = []
        for bloco in blocos:
            self.assertIn(bloco, existe, bloco)
            do_bloco = self._locs(site, bloco.lstrip("/"))
            self.assertLessEqual(len(do_bloco), 3, bloco)
            urls += do_bloco
        for caminho in urls:
            self.assertIn(caminho, existe, caminho)
        self.assertIn("/ncm/capitulo-84/pagina-2/", urls)
        # Sitemap e lastmod.json falam das mesmas páginas, sem repetição.
        self.assertEqual(len(urls), len(set(urls)))
        self.assertEqual(set(urls), {c for c, _ in _paginas_do_lastmod(_lastmod(tmp.name))})

    def test_site_fecha_sem_viradas(self):
        # Em 2100 toda data da fixture já passou: a home muda de molde, o
        # feed sai sem item, o CSV só com cabeçalho - e tudo continua válido.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        codigo, site, _ = _monta(tmp.name, "/repo", referencia=date(2100, 1, 1))
        self.assertEqual(codigo, 0)
        self._confere_fechado(site, "/repo")
        home = _le(site, "index.html")
        self.assertIn("<h1>Nenhum atributo de NCM tem virada agendada hoje</h1>", home)
        self.assertIn(
            '<p class="pendente">Nenhuma virada agendada no arquivo de hoje.</p>', home
        )
        self.assertNotIn("contagem-num", home)
        self.assertNotIn("O que mudou", home)
        feed = ET.fromstring(_le(site, "feed.xml"))
        self.assertEqual(feed.findall(".//item"), [])
        self.assertEqual(feed.findtext(".//lastBuildDate"), "Fri, 01 Jan 2100 00:00:00 GMT")
        self.assertEqual(_le(site, "dados", "viradas.csv").count("\n"), 1)
        self.assertNotIn("Com virada agendada", _le(site, "atributos", "index.html"))
        self.assertNotIn(
            "virada agendada</span>", _le(site, "orgaos", "inmetro", "index.html")
        )
        self.assertEqual(_jsonld(home)["temporalCoverage"], "2100-01-01")

    def test_site_fecha_com_config_cheia(self):
        # Domínio próprio (base_path vazio), chave IndexNow, analítica com
        # aspa no código e formulário embutido com & na URL.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = {
            "dominio": "sentinela.exemplo.test",
            "indexnow_key": "a1b2c3d4",
            "google_verificacao": "googleabc123.html",
            "goatcounter_code": 'x"y',
            "form_embed_url": "https://form.test/e?a=1&b=2",
        }
        codigo, site, _ = _monta(tmp.name, "", cfg=cfg)
        self.assertEqual(codigo, 0)
        self._confere_fechado(site, "")
        self.assertEqual(_le(site, "CNAME"), "sentinela.exemplo.test\n")
        self.assertEqual(_le(site, "a1b2c3d4.txt"), "a1b2c3d4")
        # O conteúdo é derivado do nome: o Google entrega o arquivo com uma
        # linha só, que é o próprio nome dele. Guardar as duas coisas no
        # config seria manter duas em sincronia para nada.
        self.assertEqual(
            _le(site, "googleabc123.html"),
            "google-site-verification: googleabc123.html",
        )
        home = _le(site, "index.html")
        self.assertIn(
            '<script data-goatcounter="https://x&quot;y.goatcounter.com/count" async '
            'src="https://gc.zgo.at/count.js"></script>',
            home,
        )
        self.assertNotIn('https://x"y', home)
        self.assertIn('<iframe src="https://form.test/e?a=1&amp;b=2"', home)
        self.assertNotIn("mailto:", home)
        # Os arquivos de configuração não são páginas: fora do sitemap e do
        # lastmod.
        lastmod = _lastmod(tmp.name)
        self.assertNotIn("/CNAME", lastmod)
        self.assertNotIn("/a1b2c3d4.txt", lastmod)
        self.assertNotIn("/googleabc123.html", lastmod)
        for nome in os.listdir(site):
            if nome.startswith("sitemap") and nome.endswith(".xml"):
                self.assertNotIn("CNAME", _le(site, nome))
                self.assertNotIn("a1b2c3d4", _le(site, nome))
        self.assertIn("Sitemap: https://exemplo.test/sitemap.xml", _le(site, "robots.txt"))


RE_JSONLD = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def _jsonld(html):
    """O JSON-LD da página, parseado. Exatamente um por página."""
    blocos = RE_JSONLD.findall(html)
    assert len(blocos) == 1, f"{len(blocos)} blocos de JSON-LD"
    return json.loads(blocos[0])


def _paginas_html(site):
    return [
        os.path.join(b, n) for b, _, ns in os.walk(site) for n in ns if n.endswith(".html")
    ]


class TestPublicacao(unittest.TestCase):
    """O site gerado uma vez, lido por varios testes: folha unica, roles
    das tabelas, dados abertos, JSON-LD e <time>."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.raiz = cls.tmp.name
        _, cls.site, _ = _monta(cls.raiz, "/repo")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_uma_folha_so_e_fontes_dentro(self):
        # Os @font-face vivem dentro de estilo.<hash>.css, com o base_path
        # já nos url(): uma folha bloqueante por página, não duas. Os woff2
        # continuam em site/fontes/ e os preload do <head> continuam.
        site = self.site
        folhas = [n for n in os.listdir(site) if n.endswith(".css")]
        self.assertEqual(len(folhas), 1, folhas)
        css = _le(site, folhas[0])
        self.assertTrue(css.startswith("@font-face"), css[:60])
        self.assertIn("url(/repo/fontes/figtree-latin.woff2)", css)
        self.assertIn("url(/repo/fontes/jetbrainsmono-latin.woff2)", css)
        self.assertNotIn("url(/fontes/", css)
        self.assertIn(":root{", css)
        self.assertNotIn("/*", css)
        self.assertFalse(os.path.exists(os.path.join(site, "fontes", "fontes.css")))
        self.assertTrue(os.path.exists(os.path.join(site, "fontes", "figtree-latin.woff2")))
        self.assertTrue(os.path.exists(os.path.join(site, "fontes", "OFL-Figtree.txt")))
        home = _le(site, "index.html")
        self.assertEqual(home.count('rel="stylesheet"'), 1)
        self.assertIn(f'<link rel="stylesheet" href="/repo/{folhas[0]}">', home)
        self.assertNotIn("fontes.css", home)
        self.assertIn(
            '<link rel="preload" href="/repo/fontes/figtree-latin.woff2" as="font"', home
        )
        # Sem base_path os url() ficam na raiz.
        tmp2 = tempfile.TemporaryDirectory()
        self.addCleanup(tmp2.cleanup)
        _, site2, _ = _monta(tmp2.name, "")
        folha = [n for n in os.listdir(site2) if n.endswith(".css")][0]
        self.assertIn("url(/fontes/figtree-latin.woff2)", _le(site2, folha))

    def test_tabela_tem_roles(self):
        # Abaixo de 640px o CSS poe display:block nas linhas e celulas, e o
        # navegador apaga a semantica de tabela da arvore de acessibilidade.
        # Os roles explicitos a devolvem - em TODA tabela do site.
        site = self.site
        tabelas = 0
        for caminho in _paginas_html(site):
            html = _le(caminho)
            for tag, role in (
                ("table", "table"),
                ("thead", "rowgroup"),
                ("tbody", "rowgroup"),
                ("tr", "row"),
                ("th", "columnheader"),
                ("td", "cell"),
            ):
                abertas = re.findall(rf"<{tag}\b[^>]*>", html)
                sem_role = [a for a in abertas if f'role="{role}"' not in a]
                self.assertEqual(sem_role, [], (caminho, tag))
            tabelas += html.count("<table")
        self.assertGreater(tabelas, 3)
        home = _le(site, "index.html")
        self.assertIn('<table role="table"><caption>', home)
        self.assertIn('<th scope="col" role="columnheader">NCM</th>', home)
        self.assertIn('<td role="cell" class="ncm" data-rot="NCM">', home)

    def test_dataset_lista_json_e_csv(self):
        home = _le(self.site, "index.html")
        dataset = _jsonld(home)
        self.assertEqual(dataset["@type"], "Dataset")
        distribuicao = {
            d["encodingFormat"]: d["contentUrl"] for d in dataset["distribution"]
        }
        self.assertEqual(
            distribuicao,
            {
                "application/rss+xml": "https://exemplo.test/repo/feed.xml",
                "application/json": "https://exemplo.test/repo/dados/viradas.json",
                "text/csv": "https://exemplo.test/repo/dados/viradas.csv",
            },
        )
        self.assertEqual(
            dataset["publisher"],
            {
                "@type": "Organization",
                "name": "Sentinela do Catálogo",
                "url": "https://exemplo.test/repo/",
            },
        )
        self.assertEqual(dataset["sourceOrganization"]["@type"], "GovernmentOrganization")
        self.assertIn("Receita Federal", dataset["sourceOrganization"]["name"])
        self.assertTrue(dataset["isBasedOn"].startswith("https://portalunico"))
        # Os dois arquivos existem e a home os linka, discretamente.
        self.assertTrue(os.path.exists(os.path.join(self.site, "dados", "viradas.json")))
        self.assertTrue(os.path.exists(os.path.join(self.site, "dados", "viradas.csv")))
        self.assertIn(
            '<p class="dados-abertos">Dados abertos: '
            '<a href="/repo/dados/viradas.json">JSON</a> · '
            '<a href="/repo/dados/viradas.csv">CSV</a></p>',
            home,
        )
        # Sao dados, nao paginas: fora do sitemap e do lastmod.
        for nome in os.listdir(self.site):
            if nome.startswith("sitemap") and nome.endswith(".xml"):
                self.assertNotIn("/dados/", _le(self.site, nome))
        self.assertFalse(any(c.startswith("/dados/") for c in _lastmod(self.raiz)))

    def test_csv_tem_cabecalho_e_linhas(self):
        snapshot = json.loads(_le(self.raiz, "dados", "ultimo.json"))
        texto = _le(self.site, "dados", "viradas.csv")
        linhas = list(csv.reader(io.StringIO(texto)))
        self.assertEqual(linhas[0], list(g.COLUNAS_CSV))
        self.assertEqual(len(linhas) - 1, len(snapshot["viradas"]))
        self.assertIn(
            [
                "8415.10.90",
                "ATT_FUTURO",
                "Referência de licenciamento",
                "INMETRO",
                "2099-12-31",
                "2020-01-01",
                "IMPORTACAO",
            ],
            linhas,
        )
        # O JSON e o snapshot normalizado, sem os campos volateis: a hora
        # da coleta muda a cada run e quebraria o determinismo do site.
        publico = json.loads(_le(self.site, "dados", "viradas.json"))
        self.assertEqual(publico["data_referencia"], "2026-08-22")
        self.assertEqual(len(publico["viradas"]), len(snapshot["viradas"]))
        # Normalizado: a virada sai completa (nome, orgaos), o que o
        # ultimo.json de schema 2 nao carrega - la isso mora no mapa.
        primeira = publico["viradas"][0]
        self.assertNotIn("nome", snapshot["viradas"][0])
        self.assertEqual(
            primeira["nome"], snapshot["atributos"][primeira["atributo"]]["nome"]
        )
        self.assertIn("atributos", publico)
        self.assertEqual(publico["contagens"], snapshot["contagens"])
        for chave in comum.VOLATEIS:
            self.assertNotIn(chave, publico)
            self.assertNotIn(chave, publico["http"])
        self.assertEqual(publico["http"]["sha256_json"], snapshot["http"]["sha256_json"])

    def test_time_datetime_nas_datas(self):
        site = self.site
        home = _le(site, "index.html")
        # Chapéu, cartão, célula da tabela e rodapé.
        self.assertIn(
            '<span class="chapeu">Atualizado em '
            '<time datetime="2026-08-22">22/08/2026</time></span>',
            home,
        )
        self.assertIn(
            '<div><span>corte</span><time datetime="2026-08-22">22/08/2026</time></div>',
            home,
        )
        self.assertIn(
            '<div><span>próximo</span><time datetime="2099-12-31">31/12/2099</time></div>',
            home,
        )
        self.assertIn(
            '<td role="cell" class="data" data-rot="Vira obrigatório em">'
            '<time datetime="2099-12-31">31/12/2099</time><br>',
            home,
        )
        for caminho in _paginas_html(site):
            self.assertIn(
                'Coleta automática em <time datetime="2026-08-22">22/08/2026</time>',
                _le(caminho),
                caminho,
            )
        ncm = _le(site, "ncm", "8415.10.90", "index.html")
        self.assertIn(
            '<span class="tag muda">vira obrigatório em '
            '<time datetime="2099-12-31">31/12/2099</time></span>',
            ncm,
        )


class _Estrutura(html.parser.HTMLParser):
    """O que a estrutura de uma página tem de ter, lido com o parser da
    biblioteca padrão em vez de regex: h1, title, description, canonical,
    og:url, lang, os JSON-LD, as tabelas abertas e fechadas, os hrefs."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.h1 = 0
        self.title = None
        self.description = None
        self.canonical = None
        self.og_url = None
        self.lang = None
        self.jsonld = []
        self.tabelas_abertas = 0
        self.tabelas_fechadas = 0
        self.hrefs = []
        self._dentro = None
        self._texto = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang")
        elif tag == "h1":
            self.h1 += 1
        elif tag == "title":
            self._dentro, self._texto = "title", []
        elif tag == "meta":
            if a.get("name") == "description":
                self.description = a.get("content")
            if a.get("property") == "og:url":
                self.og_url = a.get("content")
        elif tag == "link" and a.get("rel") == "canonical":
            self.canonical = a.get("href")
        elif tag == "script" and a.get("type") == "application/ld+json":
            self._dentro, self._texto = "jsonld", []
        elif tag == "table":
            self.tabelas_abertas += 1
        if tag in ("a", "link"):
            self.hrefs.append(a.get("href"))

    def handle_endtag(self, tag):
        if tag == "table":
            self.tabelas_fechadas += 1
        elif tag == "title" and self._dentro == "title":
            self.title, self._dentro = "".join(self._texto), None
        elif tag == "script" and self._dentro == "jsonld":
            self.jsonld.append("".join(self._texto))
            self._dentro = None

    def handle_data(self, data):
        if self._dentro:
            self._texto.append(data)


def _estrutura(html_texto):
    parser = _Estrutura()
    parser.feed(html_texto)
    parser.close()
    return parser


# O que a fixture carrega em ATT_HOSTIL, e como cada pedaço tem de sair no
# HTML: escapado, nunca cru.
HOSTIL = {
    "nome": "Tes<te> \"x\" & 'y'",
    "nome_html": "Tes&lt;te&gt; &quot;x&quot; &amp; &#39;y&#39;",
    "definicao_html": "&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;",
    "orientacao_html": "<p>Preencha {{formulario}} conforme a nota &lt;fiscal&gt;.</p>",
    "orgao": "ÓRGÃO <B>",
    "orgao_html": "ÓRGÃO &lt;B&gt;",
    "opcao_html": "<dt>&lt;1</dt><dd>Opção com &lt; no texto</dd>",
}


class TestHostil(unittest.TestCase):
    """ATT_HOSTIL: nome, definição, orientação, órgão e domínio com HTML,
    fechamento de <script> e um {{placeholder}} do template. O arquivo da
    Receita é entrada externa; nada dele pode chegar cru a lugar nenhum."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.raiz = cls.tmp.name
        _, cls.site, _ = _monta(cls.raiz, "/repo")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _textos(self):
        """Todo arquivo de texto publicado que não é dado bruto: {rel: texto}."""
        saida = {}
        for base, _, nomes in os.walk(self.site):
            for nome in nomes:
                if nome.endswith((".html", ".xml", ".txt")):
                    rel = "/" + os.path.relpath(os.path.join(base, nome), self.site)
                    saida[rel.replace(os.sep, "/")] = _le(base, nome)
        return saida

    def test_nada_hostil_chega_cru(self):
        textos = self._textos()
        self.assertGreater(len(textos), 10)
        for rel, texto in textos.items():
            self.assertNotIn("<te>", texto, rel)
            self.assertNotIn("</script><script>alert", texto, rel)
            self.assertNotIn("<B>", texto, rel)
            self.assertNotIn("<fiscal>", texto, rel)
            if rel.endswith(".html"):
                # O {{formulario}} do dado fica como texto, intacto, só na
                # página do atributo - e a captura de verdade continua lá.
                esperado = CHAVES_TOLERADAS.get(
                    (rel.replace("index.html", ""), "formulario"), 0
                )
                self.assertEqual(texto.count("{{formulario}}"), esperado, rel)
                self.assertEqual(texto.count('<section class="captura"'), 1, rel)
                self.assertIn("mailto:a@b.test", texto, rel)
                for bloco in RE_JSONLD.findall(texto):
                    json.loads(bloco)

        atributo = textos["/atributos/ATT_HOSTIL/index.html"]
        self.assertIn(f"<h1>{HOSTIL['nome_html']} (ATT_HOSTIL): vira", atributo)
        self.assertIn(
            f"<p>Definição hostil: {HOSTIL['definicao_html']} e segue.</p>", atributo
        )
        self.assertIn(HOSTIL["orientacao_html"], atributo)
        self.assertIn(HOSTIL["opcao_html"], atributo)
        self.assertIn(f"Atributo exigido por {HOSTIL['orgao_html']}", atributo)
        self.assertIn(f"<title>{HOSTIL['nome_html']} (ATT_HOSTIL)", atributo)
        # O JSON-LD neutraliza < > & e continua sendo o mesmo dado.
        grafo = _jsonld(atributo)
        trilha = [no for no in grafo["@graph"] if no["@type"] == "BreadcrumbList"][0]
        self.assertEqual(trilha["itemListElement"][-1]["name"], HOSTIL["nome"])
        self.assertIn("\\u003c", atributo)

        ncm = textos["/ncm/3004.90.99/index.html"]
        self.assertIn(
            f'<a href="/repo/atributos/ATT_HOSTIL/">{HOSTIL["nome_html"]}</a>', ncm
        )
        self.assertIn(f"<li><strong>{HOSTIL['nome_html']}</strong></li>", ncm)
        self.assertIn(f"Exigência do {HOSTIL['orgao_html']}.", ncm)
        self.assertIn(f'<td role="cell" data-rot="Órgão">{HOSTIL["orgao_html"]}</td>', ncm)

        home = textos["/index.html"]
        self.assertIn(
            f'<a href="/repo/atributos/ATT_HOSTIL/">{HOSTIL["nome_html"]}</a>', home
        )
        orgao = textos["/orgaos/orgao-b/index.html"]
        self.assertIn(f"<h1>Atributos exigidos pelo {HOSTIL['orgao_html']} no", orgao)
        self.assertIn(f'aria-label="Atributos exigidos pelo {HOSTIL["orgao_html"]}"', orgao)

        feed = ET.fromstring(textos["/feed.xml"])
        titulos = [i.findtext("title") for i in feed.findall(".//item")]
        self.assertIn(
            f"NCM 3004.90.99: {HOSTIL['nome']} vira obrigatório em 31/12/2099", titulos
        )
        descricoes = [i.findtext("description") for i in feed.findall(".//item")]
        self.assertTrue(any(HOSTIL["orgao"] in d for d in descricoes))

    def test_dados_abertos_carregam_o_dado_intacto(self):
        # CSV e JSON são dados, não marcação: escapá-los seria corromper o
        # nome. O csv e o json da biblioteca padrão devolvem o original.
        linhas = list(csv.reader(io.StringIO(_le(self.site, "dados", "viradas.csv"))))
        por_atributo = {linha[1]: linha for linha in linhas[1:]}
        self.assertEqual(por_atributo["ATT_HOSTIL"][2], HOSTIL["nome"])
        self.assertEqual(por_atributo["ATT_HOSTIL"][3], HOSTIL["orgao"])
        publico = json.loads(_le(self.site, "dados", "viradas.json"))
        self.assertEqual(publico["atributos"]["ATT_HOSTIL"]["nome"], HOSTIL["nome"])
        self.assertEqual(publico["atributos"]["ATT_HOSTIL"]["orgaos"], [HOSTIL["orgao"]])

    def test_estrutura_de_toda_pagina(self):
        paginas = _paginas_html(self.site)
        self.assertGreater(len(paginas), 10)
        for caminho in paginas:
            rel = os.path.relpath(caminho, self.site).replace(os.sep, "/")
            html_texto = _le(caminho)
            e = _estrutura(html_texto)
            with self.subTest(pagina=rel):
                self.assertEqual(e.lang, "pt-BR")
                self.assertEqual(e.h1, 1)
                self.assertTrue(e.title and e.title.strip())
                self.assertTrue(e.description and e.description.strip())
                self.assertNotEqual(e.title, e.description)
                if rel == "404.html":
                    self.assertIsNone(e.canonical)
                    self.assertIsNone(e.og_url)
                    self.assertEqual(e.jsonld, [])
                else:
                    self.assertIsNotNone(e.canonical)
                    self.assertEqual(e.canonical, e.og_url)
                    self.assertEqual(len(e.jsonld), 1)
                for bloco in e.jsonld:
                    self.assertIsInstance(json.loads(bloco), dict)
                self.assertEqual(e.tabelas_abertas, e.tabelas_fechadas)
                self.assertEqual(html_texto.count("<table"), e.tabelas_abertas)
                self.assertNotIn(None, e.hrefs)
                self.assertNotIn("", e.hrefs)
        # Pelo menos uma página tem tabela: o parser não está contando zero
        # por não ter visto nada.
        self.assertGreater(_estrutura(_le(self.site, "index.html")).tabelas_abertas, 0)


class TestDateModified(unittest.TestCase):
    def _datas_jsonld(self, site):
        """{caminho: dateModified} das paginas de NCM e de atributo."""
        datas = {}
        for caminho in _paginas_html(site):
            rel = "/" + os.path.relpath(caminho, site).replace(os.sep, "/")
            if not (rel.startswith("/ncm/") or rel.startswith("/atributos/")):
                continue
            if "capitulo" in rel or rel in ("/ncm/index.html", "/atributos/index.html"):
                continue
            dados = _jsonld(_le(caminho))
            nos = {no["@type"]: no for no in dados["@graph"]}
            self.assertIn("BreadcrumbList", nos, rel)
            pagina = nos["WebPage"]
            url = rel[: -len("index.html")]
            self.assertEqual(pagina["url"], "https://exemplo.test/repo" + url, rel)
            self.assertEqual(pagina["isPartOf"]["url"], "https://exemplo.test/repo/")
            datas[url] = pagina["dateModified"]
        return datas

    def test_datemodified_bate_com_lastmod(self):
        # Dia 22 e dia 23 com a mesma fixture: a pagina que nao mudou (a NCM
        # 8436.21.00, vencida nos dois dias) mantem o dateModified do dia 1,
        # a que mudou (8418.69.20: a virada de 22/08 virou prazo vencido)
        # ganha o do dia 2 - e em TODA pagina o JSON-LD, escrito antes, diz
        # o mesmo que o lastmod.json, calculado no fim.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        _, site, _ = _monta(tmp.name, "/repo", referencia=date(2026, 8, 22))
        dia1 = self._datas_jsonld(site)
        self.assertGreater(len(dia1), 10)
        self.assertTrue(all(d == "2026-08-22" for d in dia1.values()))
        primeiro = _lastmod(tmp.name)
        for caminho, data in dia1.items():
            self.assertEqual(primeiro[caminho][1], data, caminho)

        _, site, _ = _monta(tmp.name, "/repo", referencia=date(2026, 8, 23))
        dia2 = self._datas_jsonld(site)
        lastmod = _lastmod(tmp.name)
        for caminho, data in dia2.items():
            self.assertEqual(lastmod[caminho][1], data, caminho)
        self.assertEqual(dia2["/ncm/8436.21.00/"], "2026-08-22")
        self.assertEqual(dia2["/atributos/ATT_FUTURO/"], "2026-08-22")
        self.assertEqual(dia2["/ncm/8418.69.20/"], "2026-08-23")
        # O dateModified nao entra na assinatura: senao a pagina mudaria
        # de hash por causa da propria data, e nunca mais ficaria estavel.
        self.assertEqual(primeiro["/ncm/8436.21.00/"], lastmod["/ncm/8436.21.00/"])
        self.assertNotEqual(primeiro["/ncm/8418.69.20/"][0], lastmod["/ncm/8418.69.20/"][0])


class TestModoLote(unittest.TestCase):
    """A virada em massa: um atributo opcional com a mesma data em mais de
    LIMIAR_LOTE NCMs (o cClassTrib em miniatura)."""

    # Acima do limiar do lote E do teto do aviso na pagina do atributo.
    N = max(g.LIMIAR_LOTE, g.MAX_NCMS_AVISO) + 15
    # No dia 23 ATT_HOJE ja passou: sobram DUAS viradas soltas (ATT_FUTURO e
    # ATT_HOSTIL).
    SOLTAS = 2
    DIA = date(2026, 8, 23)

    def _roda(self, tmp, n=N, referencia=DIA, data=apoio.DATA_LOTE):
        codigo, site, _ = _monta(
            tmp,
            "/repo",
            referencia=referencia,
            dados=apoio.com_lote(apoio.amostra(), n, data=data),
        )
        self.assertEqual(codigo, 0)
        return site

    def test_modo_lote_agrupa_acima_do_limiar(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        site = self._roda(tmp.name)
        home = _le(site, "index.html")
        # Uma linha de lote e as soltas; a contagem segue por vinculo.
        self.assertEqual(home.count('<tr role="row" class="lote">'), 1)
        self.assertEqual(home.count('<td role="cell" class="ncm"'), self.SOLTAS)
        self.assertIn(f"<strong>{self.N} NCMs</strong>", home)
        self.assertIn(
            '<a href="/repo/atributos/ATT_LOTE/">veja as NCMs na página do atributo</a>',
            home,
        )
        total = self.N + self.SOLTAS
        self.assertIn(f"<h1>{total} atributos de NCM viram obrigatórios", home)
        self.assertIn(f"São {total} vínculos em {total} NCMs.", home)
        self.assertIn(f"<div><span>vínculos</span>{self.N} de {total}</div>", home)
        # Feed: um item para o lote, um para cada solta.
        feed = _le(site, "feed.xml")
        self.assertEqual(feed.count("<item>"), 1 + self.SOLTAS)
        self.assertIn(
            f"vira obrigatório em 01/01/2027 para {self.N} NCMs</title>"
            "<link>https://exemplo.test/repo/atributos/ATT_LOTE/</link>"
            '<guid isPermaLink="false">ATT_LOTE-2027-01-01</guid>',
            feed,
        )
        # A pagina do atributo lista ate MAX_NCMS_AVISO e remete ao indice.
        atributo = _le(site, "atributos", "ATT_LOTE", "index.html")
        aviso = atributo.split('<div class="aviso">')[1].split("</div>")[0]
        self.assertEqual(aviso.count('<li><a href="/repo/ncm/'), g.MAX_NCMS_AVISO)
        self.assertIn(
            f"<li>e mais {self.N - g.MAX_NCMS_AVISO} NCMs — "
            '<a href="/repo/ncm/">veja o índice por capítulo</a></li>',
            aviso,
        )
        # A pagina de cada NCM do lote continua individual.
        ncm = _le(site, "ncm", "9001.00.07", "index.html")
        self.assertIn("vira obrigatório em 01/01/2027", ncm)
        TestSiteFecha._confere_fechado(self, site, "/repo")

    def test_no_limiar_nao_agrupa(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        site = self._roda(tmp.name, n=g.LIMIAR_LOTE)
        home = _le(site, "index.html")
        self.assertNotIn('class="lote"', home)
        self.assertEqual(
            home.count('<td role="cell" class="ncm"'), g.LIMIAR_LOTE + self.SOLTAS
        )
        self.assertEqual(_le(site, "feed.xml").count("<item>"), g.LIMIAR_LOTE + self.SOLTAS)

    def test_o_que_mudou_tem_um_item_por_lote(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        _monta(tmp.name, "/repo", referencia=date(2026, 8, 22))
        n = g.LIMIAR_LOTE + 1
        site = self._roda(tmp.name, n=n, data="2026-09-01")
        home = _le(site, "index.html")
        self.assertIn("<h3>Viradas novas</h3>", home)
        self.assertIn(
            '<li><a href="/repo/atributos/ATT_LOTE/">Código de classificação '
            f"tributária</a> vira obrigatório em 01/09/2026 para {n} NCMs</li>",
            home,
        )
        self.assertNotIn("9001.00.07</a> — Código", home)
        # E quando o lote passa da data (dentro da janela de 30 dias do
        # historico), sai da lista em massa - um item, nao N. Nesta fixture o
        # atributo continua opcional depois da data, entao o desfecho e o do
        # meio: a data passou e a Receita nao trocou o campo.
        self._roda(tmp.name, n=n, referencia=date(2026, 9, 2), data="2026-09-01")
        home = _le(site, "index.html")
        self.assertIn("<h3>Saíram da lista</h3>", home)
        self.assertIn("<h4>Passaram da data e continuam opcionais</h4>", home)
        self.assertIn(
            f"passou da data em 01/09/2026 e continua opcional em {n} NCMs</li>", home
        )
        self.assertNotIn("9001.00.07</a> — Código", home)

    def test_mudancas_sem_teto_fora_de_rebuild(self):
        # Primeira geracao: rebuild (nao ha lastmod.json), so a raiz. Depois
        # uma virada em massa: centenas de paginas novas, e vao todas -
        # o teto de 200 URLs nao existe mais.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        _monta(tmp.name, "/repo")
        self.assertEqual(
            _le(tmp.name, "site", "mudancas.txt"), "https://exemplo.test/repo/\n"
        )
        site = self._roda(tmp.name, n=220, referencia=HOJE)
        linhas = _le(site, "mudancas.txt").splitlines()
        self.assertGreater(len(linhas), 220)
        self.assertIn("https://exemplo.test/repo/", linhas)
        self.assertIn("https://exemplo.test/repo/ncm/9001.02.19/", linhas)
        self.assertIn("https://exemplo.test/repo/atributos/ATT_LOTE/", linhas)


class TestRebuild(unittest.TestCase):
    def test_rebuild_por_template_manda_so_a_raiz(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        _monta(tmp.name, "/repo")
        primeiro = _lastmod(tmp.name)
        self.assertIn(g.CHAVE_TEMPLATES, primeiro)
        # Segunda geracao igual: nada mudou, mudancas.txt vazio.
        _monta(tmp.name, "/repo")
        self.assertEqual(_le(tmp.name, "site", "mudancas.txt"), "")
        # Um CSS diferente: o HTML de toda pagina mudou sem mudanca de dado.
        # mudancas.txt leva so a raiz e as datas do lastmod ficam.
        estilo = os.path.join(tmp.name, "templates", "estilo.css")
        with open(estilo, "a", encoding="utf-8") as f:
            f.write("\n/* outro */\n")
        _monta(tmp.name, "/repo")
        segundo = _lastmod(tmp.name)
        self.assertEqual(
            _le(tmp.name, "site", "mudancas.txt"), "https://exemplo.test/repo/\n"
        )
        self.assertNotEqual(primeiro[g.CHAVE_TEMPLATES], segundo[g.CHAVE_TEMPLATES])
        for chave in primeiro:
            if chave != g.CHAVE_TEMPLATES:
                self.assertEqual(primeiro[chave], segundo[chave], chave)

    def test_chave_templates_nao_vira_url(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        _, site, _ = _monta(tmp.name, "/repo")
        self.assertIsInstance(_lastmod(tmp.name)[g.CHAVE_TEMPLATES], str)
        for nome in os.listdir(site):
            if nome.endswith(".xml") or nome == "mudancas.txt":
                self.assertNotIn("__", _le(site, nome), nome)
        self.assertFalse(os.path.exists(os.path.join(site, g.CHAVE_TEMPLATES)))


class TestDadosSeparados(unittest.TestCase):
    """--dados aponta o gerador para outra árvore de dados.

    É o que a branch órfã 'dados' precisa: o workflow faz dois checkouts (o
    código em main, o dado na branch) e o gerador tem de ler templates,
    fontes e config.json de um lugar e dados/ do outro. O teste prova a
    separação pelos dois lados - o dado vem de fora, o template vem da raiz.
    """

    def _monta_separado(self):
        raiz = tempfile.TemporaryDirectory()
        self.addCleanup(raiz.cleanup)
        fora = tempfile.TemporaryDirectory()
        self.addCleanup(fora.cleanup)
        with apoio.ambiente(raiz.name, {"base_path": "/repo"}) as caminhos:
            # A raiz fica com um dados/ VAZIO (o ambiente cria o histórico):
            # se o gerador lesse dali, montar_build não acharia ultimo.json e
            # main() devolveria 1. O sucesso é a prova de que leu de --dados.
            de_fora = comum.Caminhos(
                raiz=caminhos.raiz, dados=os.path.join(fora.name, "dados")
            )
            os.makedirs(de_fora.historico)
            apoio.montar_dados(de_fora, apoio.amostra(), HOJE)
            # Uma marca só no template da raiz: se o gerador tivesse levado
            # também os templates para junto do dado, ela não apareceria.
            base = os.path.join(caminhos.templates, "base.html")
            with open(base, "a", encoding="utf-8") as f:
                print("<!-- marca-da-raiz -->", file=f)
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                codigo = g.main(["--raiz", raiz.name, "--dados", de_fora.dados])
        return codigo, raiz.name, de_fora

    def test_le_os_dados_de_fora_e_os_templates_da_raiz(self):
        codigo, raiz, de_fora = self._monta_separado()
        self.assertEqual(codigo, 0)
        home = _le(raiz, "site", "index.html")
        self.assertIn("<!-- marca-da-raiz -->", home)
        # O dado que so existe fora chegou ao site.
        self.assertIn(g.br(HOJE.isoformat()), home)

    def test_lastmod_e_gravado_junto_do_dado_e_nao_na_raiz(self):
        """O lastmod acompanha o dado - é ele que o workflow commita na branch.

        Se saísse em <raiz>/dados/, o passo de commit da branch nunca o veria
        e o gerador acharia que tudo mudou todo dia: lastmod de hoje em 11
        mil URLs, que é exatamente o que o arquivo existe para evitar.
        """
        _, raiz, de_fora = self._monta_separado()
        self.assertTrue(os.path.exists(de_fora.lastmod))
        self.assertFalse(os.path.exists(os.path.join(raiz, "dados", "lastmod.json")))


if __name__ == "__main__":
    unittest.main()
