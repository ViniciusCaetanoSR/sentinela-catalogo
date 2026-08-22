"""Testes das funcoes puras do gerador. Sem rede e sem escrever no site/."""

import json
import os
import sys
import unittest
import xml.etree.ElementTree as ET
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gerar_site as g  # noqa: E402

CFG = {"base_url": "https://exemplo.test", "base_path": "/repo",
       "contato_email": "", "form_embed_url": "", "goatcounter_code": "",
       "dominio": "", "indexnow_key": ""}
SEM_PREFIXO = dict(CFG, base_path="")


class TestEsc(unittest.TestCase):
    def test_escapa_o_basico(self):
        self.assertEqual(g.esc('<a href="x">&</a>'),
                         "&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;")

    def test_escapa_aspa_simples(self):
        # Sem isto, valor interpolado dentro de atributo com aspas simples
        # escapa do atributo.
        self.assertEqual(g.esc("d'agua"), "d&#39;agua")

    def test_none_vira_vazio(self):
        self.assertEqual(g.esc(None), "")

    def test_e_comercial_vem_primeiro(self):
        # Se & fosse escapado por ultimo, &lt; viraria &amp;lt;.
        self.assertEqual(g.esc("<"), "&lt;")


class TestUrl(unittest.TestCase):
    def test_aplica_o_prefixo(self):
        self.assertEqual(g.url(CFG, "/ncm/8415.10.90/"), "/repo/ncm/8415.10.90/")

    def test_sem_prefixo(self):
        self.assertEqual(g.url(SEM_PREFIXO, "/ncm/x/"), "/ncm/x/")

    def test_absoluta_nao_duplica_o_prefixo(self):
        # O bug irmao do dos 17 links: com base_url vazio o canonical saia
        # com o prefixo, e a cirurgia de string aplicava outro por cima.
        self.assertEqual(g.absoluta(CFG, "/x/"), "https://exemplo.test/repo/x/")
        self.assertEqual(g.absoluta(dict(CFG, base_url=""), "/x/"), "/repo/x/")

    def test_base_url_com_barra_no_fim(self):
        self.assertEqual(g.absoluta(dict(CFG, base_url="https://e.test/"), "/x/"),
                         "https://e.test/repo/x/")


class TestPreencher(unittest.TestCase):
    def test_substitui(self):
        self.assertEqual(g.preencher("a {{x}} b", {"x": "1"}), "a 1 b")

    def test_nao_reescaneia_a_propria_saida(self):
        # O laco de str.replace anterior trocava um {{b}} que viesse DENTRO
        # do valor de {{a}} - texto oficial injetando bloco do template.
        self.assertEqual(
            g.preencher("{{a}}|{{b}}", {"a": "{{b}}", "b": "BOOM"}),
            "{{b}}|BOOM")

    def test_chave_desconhecida_fica_intacta(self):
        self.assertEqual(g.preencher("{{z}}", {"a": "1"}), "{{z}}")


class TestFormatadores(unittest.TestCase):
    def test_br(self):
        self.assertEqual(g.br("2026-08-30"), "30/08/2026")
        self.assertEqual(g.br(""), "")
        self.assertEqual(g.br(None), "")

    def test_por_extenso(self):
        self.assertEqual(g.por_extenso("2026-08-30"), "30 de agosto de 2026")
        self.assertEqual(g.por_extenso(""), "")

    def test_plural(self):
        self.assertEqual(g.plural(1, "dia", "dias"), "dia")
        self.assertEqual(g.plural(0, "dia", "dias"), "dias")
        self.assertEqual(g.plural(2, "dia", "dias"), "dias")

    def test_milhar(self):
        self.assertEqual(g.milhar(1234567), "1.234.567")
        self.assertEqual(g.milhar(0), "0")

    def test_dias_ate(self):
        self.assertEqual(g.dias_ate("2026-08-22", date(2026, 8, 22)), 0)
        self.assertEqual(g.dias_ate("2026-08-30", date(2026, 8, 22)), 8)

    def test_capitulo(self):
        self.assertEqual(g.capitulo("8415.10.90"), "84")
        self.assertEqual(g.capitulo("0101.21.00"), "01")
        self.assertEqual(g.capitulo(""), "00")


class TestJsonLd(unittest.TestCase):
    def test_neutraliza_fechamento_de_script(self):
        # json.dumps nao escapa "<". Ja existem 26 campos com "<" nos dados.
        saida = g.bloco_jsonld({"nome": "</script><script>alert(1)</script>"})
        self.assertNotIn("</script><script>", saida)
        self.assertIn("\\u003c", saida)

    def test_vazio_nao_emite_tag(self):
        self.assertEqual(g.bloco_jsonld(None), "")

    def test_continua_json_valido(self):
        saida = g.bloco_jsonld({"a": "x<y>z&w"})
        corpo = saida.split(">", 1)[1].rsplit("<", 1)[0]
        self.assertEqual(json.loads(corpo)["a"], "x<y>z&w")


class TestTrilha(unittest.TestCase):
    ITENS = [("Início", "/"), ("NCMs", "/ncm/"), ("NCM 1", "/ncm/1/")]

    def test_dados_estruturados(self):
        d = g.trilha_dados(CFG, self.ITENS)
        self.assertEqual(d["@type"], "BreadcrumbList")
        self.assertEqual([i["position"] for i in d["itemListElement"]], [1, 2, 3])
        self.assertTrue(all(i["item"].startswith("https://exemplo.test/repo")
                            for i in d["itemListElement"]))

    def test_versao_visivel_existe(self):
        # O JSON-LD sem breadcrumb visivel e descartado pelo Google.
        html = g.trilha_html(CFG, self.ITENS)
        self.assertIn("<nav class=\"trilha\"", html)
        self.assertIn("/repo/ncm/", html)
        self.assertIn('aria-current="page"', html)

    def test_ultimo_item_nao_e_link(self):
        html = g.trilha_html(CFG, self.ITENS)
        self.assertNotIn('href="/repo/ncm/1/"', html)


class TestFormulario(unittest.TestCase):
    def test_iframe_quando_ha_embed(self):
        s = g.bloco_formulario(dict(CFG, form_embed_url="https://t.test/e"))
        self.assertIn("<iframe", s)

    def test_mailto_quando_ha_email(self):
        s = g.bloco_formulario(dict(CFG, contato_email="a@b.test"))
        self.assertIn("mailto:a@b.test", s)
        self.assertIn("%0A", s)          # corpo percent-encoded
        self.assertIn("&amp;body=", s)   # & escapado no HTML

    def test_placeholder_quando_nao_ha_nada(self):
        self.assertIn("pendente", g.bloco_formulario(CFG))


class TestConfig(unittest.TestCase):
    def test_conjunto_de_chaves_documentado(self):
        # Este teste sozinho impede o README de voltar a divergir do codigo.
        esperado = {"base_url", "base_path", "form_embed_url", "contato_email",
                    "goatcounter_code", "dominio", "indexnow_key"}
        real = set(g.config())
        self.assertTrue(esperado <= real, f"faltam {esperado - real}")
        readme = os.path.join(g.RAIZ, "README.md")
        with open(readme, encoding="utf-8") as f:
            texto = f.read()
        for chave in esperado:
            self.assertIn(chave, texto, f"{chave} nao esta documentado no README")


class TestHistorico(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.antigo = g.DIR_HISTORICO
        g.DIR_HISTORICO = self.tmp.name

    def tearDown(self):
        g.DIR_HISTORICO = self.antigo
        self.tmp.cleanup()

    def _grava(self, nome, corpo):
        with open(os.path.join(self.tmp.name, nome), "w", encoding="utf-8") as f:
            f.write(corpo)

    def test_sem_arquivo_suficiente(self):
        self.assertEqual(g.bloco_historico(CFG, date(2026, 8, 22)), "")

    def test_arquivo_corrompido_e_ignorado(self):
        self._grava("2026-08-20.json", "{ isto nao e json")
        self._grava("2026-08-21.json", json.dumps({"viradas": []}))
        self._grava("2026-08-22.json", json.dumps({"viradas": [
            {"ncm": "1", "atributo": "A", "nome": "N",
             "vira_obrigatorio_em": "2099-01-01"}]}))
        html = g.bloco_historico(CFG, date(2026, 8, 22))
        self.assertIn("Viradas novas", html)

    def test_schema_desconhecido_e_ignorado(self):
        # O formato ja mudou uma vez (atributos_destaque sumiu no dia 2).
        self._grava("2026-08-21.json", json.dumps({"outra_coisa": 1}))
        self._grava("2026-08-22.json", json.dumps({"viradas": [
            {"ncm": "1", "atributo": "A", "nome": "N",
             "vira_obrigatorio_em": "2099-01-01"}]}))
        g.bloco_historico(CFG, date(2026, 8, 22))

    def test_janela_e_de_dias_e_nao_de_arquivos(self):
        self._grava("2020-01-01.json", json.dumps({"viradas": [
            {"ncm": "9", "atributo": "Z", "nome": "Velho",
             "vira_obrigatorio_em": "2020-02-01"}]}))
        self._grava("2026-08-21.json", json.dumps({"viradas": []}))
        self._grava("2026-08-22.json", json.dumps({"viradas": []}))
        # O de 2020 esta fora dos 30 dias: nao pode virar "saiu da lista".
        self.assertEqual(g.bloco_historico(CFG, date(2026, 8, 22)), "")

    def test_saiu_da_lista_mostra_nome_e_nao_codigo(self):
        self._grava("2026-08-21.json", json.dumps({"viradas": [
            {"ncm": "1", "atributo": "ATT_9", "nome": "Nome Legivel",
             "vira_obrigatorio_em": "2026-08-21"}]}))
        self._grava("2026-08-22.json", json.dumps({"viradas": []}))
        html = g.bloco_historico(CFG, date(2026, 8, 22))
        self.assertIn("Nome Legivel", html)
        self.assertNotIn("ATT_9", html)


class TestFeed(unittest.TestCase):
    SNAP = {"data_referencia": "2026-08-22",
            "contagens": {"versao": "346"},
            "viradas": [{"ncm": "8415.10.90", "atributo": "ATT_1",
                         "nome": "Teste", "orgaos": ["INMETRO"],
                         "vira_obrigatorio_em": "2026-08-30"}]}

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.antigo = g.DIR_SITE
        g.DIR_SITE = self.tmp.name

    def tearDown(self):
        g.DIR_SITE = self.antigo
        self.tmp.cleanup()

    def _feed(self, snap):
        g.gerar_feed(CFG, snap)
        with open(os.path.join(self.tmp.name, "feed.xml"), encoding="utf-8") as f:
            return f.read()

    def test_xml_valido(self):
        ET.fromstring(self._feed(self.SNAP))

    def test_tem_link_self(self):
        xml = self._feed(self.SNAP)
        self.assertIn('rel="self"', xml)

    def test_sem_viradas_continua_valido(self):
        vazio = dict(self.SNAP, viradas=[])
        raiz = ET.fromstring(self._feed(vazio))
        self.assertEqual(raiz.findall(".//item"), [])

    def test_guid_estavel_entre_execucoes(self):
        self.assertEqual(self._feed(self.SNAP), self._feed(self.SNAP))


class TestPng(unittest.TestCase):
    def test_assinatura_e_dimensoes(self):
        import struct
        d = g._png_solido(10, 4, [(0, 0, 5, 2, (255, 0, 0))], (0, 0, 0))
        self.assertEqual(d[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", d[16:24]), (10, 4))


if __name__ == "__main__":
    unittest.main()
