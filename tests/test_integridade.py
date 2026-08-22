"""Gera o site inteiro a partir da fixture e confere que ele fecha.

E o teste de maior valor da suite: teria pego os 17 links quebrados que
ficaram meses no ar, e e o que segura o corte das paginas quase-duplicadas -
cortar um atributo sem parar de linkar para ele quebraria o site em silencio.
"""

import contextlib
import io
import json
import os
import re
import struct
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import coletor  # noqa: E402
import gerar_site as g  # noqa: E402

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "amostra.json"
)
HOJE = date(2026, 8, 22)

RE_LINK = re.compile(r'(?:href|src)="([^"]+)"')
EXTERNO = ("http://", "https://", "mailto:", "//", "#", "data:")


def _monta(destino, base_path, referencia=HOJE, ajustar=None):
    """Roda coletor (sem rede) e gerador contra um diretorio temporario.

    ajustar(snapshot, catalogo, completo) roda antes da gravacao, para os
    testes que precisam de um dado deliberadamente errado.
    """
    with open(FIXTURE, encoding="utf-8") as f:
        dados = json.load(f)

    vs = coletor.viradas(dados, referencia)
    publicaveis = coletor.atributos_publicaveis(dados, vs)
    snapshot = {
        "schema": coletor.SCHEMA,
        "coletado_em": f"{referencia.isoformat()}T06:00:00-03:00",
        "data_referencia": referencia.isoformat(),
        "fonte": coletor.URL,
        "http": {"status": 200},
        "contagens": coletor.contagens(dados, referencia),
        "viradas": vs,
        "ncms_afetadas": coletor.ncms_afetadas(dados, vs),
    }
    catalogo = {
        "versao": dados.get("versao"),
        "atributos": publicaveis,
        "orgaos": coletor.orgaos(publicaveis),
    }
    com_pagina = {a["codigo"] for a in publicaveis}
    completo = coletor.mapa_completo(dados, com_pagina)
    if ajustar:
        ajustar(snapshot, catalogo, completo)

    dados_dir = os.path.join(destino, "dados")
    os.makedirs(os.path.join(dados_dir, "historico"), exist_ok=True)
    for nome, corpo in (
        ("ultimo.json", snapshot),
        ("atributos.json", catalogo),
        ("completo.json", completo),
    ):
        with open(os.path.join(dados_dir, nome), "w", encoding="utf-8") as f:
            json.dump(corpo, f, ensure_ascii=False)

    cfg = os.path.join(destino, "config.json")
    with open(cfg, "w", encoding="utf-8") as f:
        json.dump(
            {
                "base_url": "https://exemplo.test",
                "base_path": base_path,
                "contato_email": "a@b.test",
            },
            f,
        )

    guardado = {
        n: getattr(g, n)
        for n in (
            "DIR_SITE",
            "DIR_HISTORICO",
            "ARQ_ULTIMO",
            "ARQ_CONFIG",
            "ARQ_ATRIBUTOS",
            "ARQ_COMPLETO",
            "ARQ_LASTMOD",
        )
    }
    g.DIR_SITE = os.path.join(destino, "site")
    g.DIR_HISTORICO = os.path.join(dados_dir, "historico")
    g.ARQ_ULTIMO = os.path.join(dados_dir, "ultimo.json")
    g.ARQ_CONFIG = cfg
    g.ARQ_ATRIBUTOS = os.path.join(dados_dir, "atributos.json")
    g.ARQ_COMPLETO = os.path.join(dados_dir, "completo.json")
    g.ARQ_LASTMOD = os.path.join(dados_dir, "lastmod.json")
    try:
        # O gerador fala bastante; nos testes o que importa e o resultado.
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            codigo = g.main()
    finally:
        for n, v in guardado.items():
            setattr(g, n, v)
    return codigo, os.path.join(destino, "site"), com_pagina


def _le(*partes):
    with open(os.path.join(*partes), encoding="utf-8") as f:
        return f.read()


def _lastmod(destino):
    return json.loads(_le(destino, "dados", "lastmod.json"))


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
    def _roda(self, base_path):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        codigo, site, com_pagina = _monta(tmp.name, base_path)
        self.assertEqual(codigo, 0)
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
            if "{{" in html:
                placeholders.append(caminho)
            for alvo in RE_LINK.findall(html):
                if alvo.startswith(EXTERNO):
                    continue
                if base_path and not alvo.startswith(base_path):
                    sem_prefixo.append((caminho, alvo))
                    continue
                local = alvo[len(base_path) :].split("#")[0] if base_path else alvo
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

    def test_assinatura_nao_muda_com_os_dias(self):
        # Mesmas viradas, outro dia de referencia: "Faltam N dias" muda no
        # HTML, mas o lastmod da home e da NCM com virada tem de ficar.
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
        ):
            self.assertIn(caminho, primeiro)
            self.assertEqual(primeiro[caminho], segundo[caminho], caminho)
        self.assertEqual(primeiro, segundo)

    def test_js_nao_e_alterado(self):
        # A remocao de comentarios nao entende string nem regex de JS: o
        # app.js e servido como esta no template.
        site, _ = self._roda("/repo")
        servidos = [
            n for n in os.listdir(site) if n.startswith("app.") and n.endswith(".js")
        ]
        self.assertEqual(len(servidos), 1)
        self.assertEqual(_le(site, servidos[0]), _le(g.DIR_TEMPLATES, "app.js"))
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
            com_marca += html.count('<td data-rot="Situação"><span class="tag muda">')
            sem_marca += html.count("<td></td>")
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


if __name__ == "__main__":
    unittest.main()
