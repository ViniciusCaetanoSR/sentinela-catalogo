"""Gera o site inteiro a partir da fixture e confere que ele fecha.

E o teste de maior valor da suite: teria pego os 17 links quebrados que
ficaram meses no ar, e e o que segura o corte das paginas quase-duplicadas -
cortar um atributo sem parar de linkar para ele quebraria o site em silencio.
"""

import json
import os
import contextlib
import io
import re
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import coletor  # noqa: E402
import gerar_site as g  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "amostra.json")
HOJE = date(2026, 8, 22)

RE_LINK = re.compile(r'(?:href|src)="([^"]+)"')
EXTERNO = ("http://", "https://", "mailto:", "//", "#", "data:")


def _monta(destino, base_path):
    """Roda coletor (sem rede) e gerador contra um diretorio temporario."""
    with open(FIXTURE, encoding="utf-8") as f:
        dados = json.load(f)

    vs = coletor.viradas(dados, HOJE)
    publicaveis = coletor.atributos_publicaveis(dados, vs)
    snapshot = {
        "schema": coletor.SCHEMA,
        "coletado_em": "2026-08-22T06:00:00-03:00",
        "data_referencia": HOJE.isoformat(),
        "fonte": coletor.URL,
        "http": {"status": 200},
        "contagens": coletor.contagens(dados, HOJE),
        "viradas": vs,
        "ncms_afetadas": coletor.ncms_afetadas(dados, vs),
    }
    catalogo = {"versao": dados.get("versao"),
                "atualizado_em": HOJE.isoformat(),
                "atributos": publicaveis,
                "orgaos": coletor.orgaos(publicaveis)}
    com_pagina = {a["codigo"] for a in publicaveis}
    completo = coletor.mapa_completo(dados, com_pagina)

    dados_dir = os.path.join(destino, "dados")
    os.makedirs(os.path.join(dados_dir, "historico"), exist_ok=True)
    for nome, corpo in (("ultimo.json", snapshot),
                        ("atributos.json", catalogo),
                        ("completo.json", completo)):
        with open(os.path.join(dados_dir, nome), "w", encoding="utf-8") as f:
            json.dump(corpo, f, ensure_ascii=False)

    cfg = os.path.join(destino, "config.json")
    with open(cfg, "w", encoding="utf-8") as f:
        json.dump({"base_url": "https://exemplo.test", "base_path": base_path,
                   "contato_email": "a@b.test"}, f)

    guardado = {n: getattr(g, n) for n in
                ("DIR_SITE", "DIR_HISTORICO", "ARQ_ULTIMO", "ARQ_CONFIG",
                 "ARQ_ATRIBUTOS", "ARQ_COMPLETO", "ARQ_LASTMOD")}
    g.DIR_SITE = os.path.join(destino, "site")
    g.DIR_HISTORICO = os.path.join(dados_dir, "historico")
    g.ARQ_ULTIMO = os.path.join(dados_dir, "ultimo.json")
    g.ARQ_CONFIG = cfg
    g.ARQ_ATRIBUTOS = os.path.join(dados_dir, "atributos.json")
    g.ARQ_COMPLETO = os.path.join(dados_dir, "completo.json")
    g.ARQ_LASTMOD = os.path.join(dados_dir, "lastmod.json")
    try:
        # O gerador fala bastante; nos testes o que importa e o resultado.
        with contextlib.redirect_stdout(io.StringIO()):
            codigo = g.main()
    finally:
        for n, v in guardado.items():
            setattr(g, n, v)
    return codigo, os.path.join(destino, "site"), com_pagina


def _arquivos(raiz):
    """Todo caminho servivel, incluindo a forma sem index.html."""
    existe = set()
    for base, _, nomes in os.walk(raiz):
        for nome in nomes:
            rel = "/" + os.path.relpath(os.path.join(base, nome),
                                        raiz).replace(os.sep, "/")
            existe.add(rel)
            if rel.endswith("/index.html"):
                existe.add(rel[:-len("index.html")])
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
        site, com_pagina = self._roda(base_path)
        existe = _arquivos(site)
        paginas = [os.path.join(b, n)
                   for b, _, ns in os.walk(site) for n in ns
                   if n.endswith(".html")]
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
                local = alvo[len(base_path):].split("#")[0] if base_path else alvo
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
        self.assertTrue(alvos <= com_pagina,
                        f"linka para atributo sem pagina: {alvos - com_pagina}")

    def test_conteudo_do_atributo_cortado_aparece_na_ncm(self):
        # ATT_CLONE_A perdeu a pagina propria; as opcoes dele tem de estar
        # visiveis na pagina da NCM 8609.00.00.
        site, _ = self._roda("/repo")
        with open(os.path.join(site, "ncm", "8609.00.00", "index.html"),
                  encoding="utf-8") as f:
            html = f.read()
        self.assertIn("Escolher apenas um Destaque", html)
        self.assertIn("EXCETO DE ESP", html)

    def test_404_fora_do_sitemap(self):
        site, _ = self._roda("/repo")
        self.assertTrue(os.path.exists(os.path.join(site, "404.html")))
        for nome in os.listdir(site):
            if nome.startswith("sitemap") and nome.endswith(".xml"):
                with open(os.path.join(site, nome), encoding="utf-8") as f:
                    self.assertNotIn("/404", f.read())

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
        self.assertFalse(os.path.exists(
            os.path.join(site, "ncm", "9999.99.99", "index.html")))
        self.assertTrue(os.path.exists(
            os.path.join(site, "ncm", "8703.10.00", "index.html")))

    def test_lastmod_so_muda_quando_o_conteudo_muda(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        _monta(tmp.name, "/repo")
        alvo = os.path.join(tmp.name, "dados", "lastmod.json")
        with open(alvo, encoding="utf-8") as f:
            primeiro = json.load(f)
        _monta(tmp.name, "/repo")
        with open(alvo, encoding="utf-8") as f:
            segundo = json.load(f)
        self.assertEqual(primeiro, segundo)


if __name__ == "__main__":
    unittest.main()
