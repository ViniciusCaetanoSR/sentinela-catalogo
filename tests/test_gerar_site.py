"""Testes das funcoes puras do gerador. Sem rede e sem escrever no site/."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gerar_site as g  # noqa: E402

CFG = {
    "base_url": "https://exemplo.test",
    "base_path": "/repo",
    "contato_email": "",
    "form_embed_url": "",
    "goatcounter_code": "",
    "dominio": "",
    "indexnow_key": "",
}
SEM_PREFIXO = dict(CFG, base_path="")


class TestEsc(unittest.TestCase):
    def test_escapa_o_basico(self):
        self.assertEqual(
            g.esc('<a href="x">&</a>'), "&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;"
        )

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
        self.assertEqual(
            g.absoluta(dict(CFG, base_url="https://e.test/"), "/x/"),
            "https://e.test/repo/x/",
        )


class TestPreencher(unittest.TestCase):
    def test_substitui(self):
        self.assertEqual(g.preencher("a {{x}} b", {"x": "1"}), "a 1 b")

    def test_nao_reescaneia_a_propria_saida(self):
        # O laco de str.replace anterior trocava um {{b}} que viesse DENTRO
        # do valor de {{a}} - texto oficial injetando bloco do template.
        self.assertEqual(
            g.preencher("{{a}}|{{b}}", {"a": "{{b}}", "b": "BOOM"}), "{{b}}|BOOM"
        )

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
        self.assertTrue(
            all(
                i["item"].startswith("https://exemplo.test/repo")
                for i in d["itemListElement"]
            )
        )

    def test_versao_visivel_existe(self):
        # O JSON-LD sem breadcrumb visivel e descartado pelo Google.
        html = g.trilha_html(CFG, self.ITENS)
        self.assertIn('<nav class="trilha"', html)
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
        self.assertIn("%0A", s)  # corpo percent-encoded
        self.assertIn("&amp;body=", s)  # & escapado no HTML

    def test_placeholder_quando_nao_ha_nada(self):
        self.assertIn("pendente", g.bloco_formulario(CFG))


class TestConfig(unittest.TestCase):
    def test_conjunto_de_chaves_documentado(self):
        # Este teste sozinho impede o README de voltar a divergir do codigo.
        esperado = {
            "base_url",
            "base_path",
            "form_embed_url",
            "contato_email",
            "goatcounter_code",
            "dominio",
            "indexnow_key",
        }
        real = set(g.config())
        self.assertTrue(esperado <= real, f"faltam {esperado - real}")
        readme = os.path.join(g.RAIZ, "README.md")
        with open(readme, encoding="utf-8") as f:
            texto = f.read()
        for chave in esperado:
            self.assertIn(chave, texto, f"{chave} nao esta documentado no README")


class TestHistorico(unittest.TestCase):
    def setUp(self):
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
        self._grava(
            "2026-08-22.json",
            json.dumps(
                {
                    "viradas": [
                        {
                            "ncm": "1",
                            "atributo": "A",
                            "nome": "N",
                            "vira_obrigatorio_em": "2099-01-01",
                        }
                    ]
                }
            ),
        )
        html = g.bloco_historico(CFG, date(2026, 8, 22))
        self.assertIn("Viradas novas", html)

    def test_schema_desconhecido_e_ignorado(self):
        # O formato ja mudou uma vez (atributos_destaque sumiu no dia 2).
        self._grava("2026-08-21.json", json.dumps({"outra_coisa": 1}))
        self._grava(
            "2026-08-22.json",
            json.dumps(
                {
                    "viradas": [
                        {
                            "ncm": "1",
                            "atributo": "A",
                            "nome": "N",
                            "vira_obrigatorio_em": "2099-01-01",
                        }
                    ]
                }
            ),
        )
        g.bloco_historico(CFG, date(2026, 8, 22))

    def test_janela_e_de_dias_e_nao_de_arquivos(self):
        self._grava(
            "2020-01-01.json",
            json.dumps(
                {
                    "viradas": [
                        {
                            "ncm": "9",
                            "atributo": "Z",
                            "nome": "Velho",
                            "vira_obrigatorio_em": "2020-02-01",
                        }
                    ]
                }
            ),
        )
        self._grava("2026-08-21.json", json.dumps({"viradas": []}))
        self._grava("2026-08-22.json", json.dumps({"viradas": []}))
        # O de 2020 esta fora dos 30 dias: nao pode virar "saiu da lista".
        self.assertEqual(g.bloco_historico(CFG, date(2026, 8, 22)), "")

    def test_saiu_da_lista_mostra_nome_e_nao_codigo(self):
        self._grava(
            "2026-08-21.json",
            json.dumps(
                {
                    "viradas": [
                        {
                            "ncm": "1",
                            "atributo": "ATT_9",
                            "nome": "Nome Legivel",
                            "vira_obrigatorio_em": "2026-08-21",
                        }
                    ]
                }
            ),
        )
        self._grava("2026-08-22.json", json.dumps({"viradas": []}))
        html = g.bloco_historico(CFG, date(2026, 8, 22))
        self.assertIn("Nome Legivel", html)
        self.assertNotIn("ATT_9", html)

    def test_saiu_da_lista_vira_link_quando_ncm_tem_pagina(self):
        self._grava(
            "2026-08-21.json",
            json.dumps(
                {
                    "viradas": [
                        {
                            "ncm": "1",
                            "atributo": "A",
                            "nome": "Com pagina",
                            "vira_obrigatorio_em": "2026-08-21",
                        },
                        {
                            "ncm": "2",
                            "atributo": "B",
                            "nome": "Sem pagina",
                            "vira_obrigatorio_em": "2026-08-21",
                        },
                    ]
                }
            ),
        )
        self._grava(
            "2026-08-22.json",
            json.dumps(
                {
                    "viradas": [
                        {
                            "ncm": "3",
                            "atributo": "C",
                            "nome": "Nova",
                            "vira_obrigatorio_em": "2099-01-01",
                        }
                    ]
                }
            ),
        )
        html = g.bloco_historico(CFG, date(2026, 8, 22), {"1", "3"})
        self.assertIn('<li><a href="/repo/ncm/1/">1</a> — Com pagina</li>', html)
        self.assertIn("<li>2 — Sem pagina</li>", html)
        self.assertIn(
            '<li><a href="/repo/ncm/3/">3</a> — Nova, a partir de 01/01/2099', html
        )
        # Sem o conjunto, nada vira link: o site tem de continuar fechado.
        self.assertNotIn("<a ", g.bloco_historico(CFG, date(2026, 8, 22)))

    def test_schema_acima_do_suportado_e_ignorado_com_aviso(self):
        self._grava(
            "2026-08-21.json",
            json.dumps(
                {
                    "schema": g.SCHEMA_SUPORTADO + 1,
                    "viradas": [
                        {
                            "ncm": "1",
                            "atributo": "A",
                            "nome": "N",
                            "vira_obrigatorio_em": "2099-01-01",
                        }
                    ],
                }
            ),
        )
        self._grava(
            "2026-08-22.json",
            json.dumps(
                {
                    "schema": g.SCHEMA_SUPORTADO,
                    "viradas": [
                        {
                            "ncm": "1",
                            "atributo": "A",
                            "nome": "N",
                            "vira_obrigatorio_em": "2099-01-01",
                        }
                    ],
                }
            ),
        )
        erro = io.StringIO()
        with contextlib.redirect_stderr(erro):
            html = g.bloco_historico(CFG, date(2026, 8, 22))
        # O arquivo de ontem foi ignorado, entao a virada parece nova.
        self.assertIn("Viradas novas", html)
        self.assertIn("schema", erro.getvalue())
        self.assertIn("2026-08-21.json", erro.getvalue())

    def test_primeira_vista_e_a_data_mais_antiga(self):
        virada = {"ncm": "1", "atributo": "A", "vira_obrigatorio_em": "2099-01-01"}
        adiada = dict(virada, vira_obrigatorio_em="2099-02-01")
        self._grava("2026-07-01.json", json.dumps({"viradas": [virada]}))
        self._grava("2026-08-21.json", json.dumps({"viradas": [virada, adiada]}))
        self._grava("2026-08-22.json", json.dumps({"viradas": [adiada]}))
        self._grava(
            "2026-08-23.json", json.dumps({"viradas": [dict(virada, ncm="futuro")]})
        )
        self._grava("notas.json", json.dumps({"viradas": [dict(virada, ncm="x")]}))
        vista = g.primeira_vista(date(2026, 8, 22))
        # Fora da janela de 30 dias, mas dentro do historico: conta.
        self.assertEqual(vista[("1", "A", "2099-01-01")], "2026-07-01")
        # Adiamento e chave nova.
        self.assertEqual(vista[("1", "A", "2099-02-01")], "2026-08-21")
        # Arquivo depois da referencia e nome que nao e data ficam de fora.
        self.assertNotIn(("futuro", "A", "2099-01-01"), vista)
        self.assertNotIn(("x", "A", "2099-01-01"), vista)


class TestFeed(unittest.TestCase):
    SNAP = {
        "data_referencia": "2026-08-22",
        "contagens": {"versao": "346"},
        "viradas": [
            {
                "ncm": "8415.10.90",
                "atributo": "ATT_1",
                "nome": "Teste",
                "orgaos": ["INMETRO"],
                "vira_obrigatorio_em": "2026-08-30",
            }
        ],
    }

    def setUp(self):
        # Site e historico num diretorio temporario: o feed le o historico
        # real se nao for desviado, e o teste deixaria de ser hermetico.
        self.tmp = tempfile.TemporaryDirectory()
        self.antigo = (g.DIR_SITE, g.DIR_HISTORICO)
        g.DIR_SITE = os.path.join(self.tmp.name, "site")
        g.DIR_HISTORICO = os.path.join(self.tmp.name, "historico")
        os.makedirs(g.DIR_HISTORICO)

    def tearDown(self):
        g.DIR_SITE, g.DIR_HISTORICO = self.antigo
        self.tmp.cleanup()

    def _historico(self, nome, viradas):
        with open(os.path.join(g.DIR_HISTORICO, nome), "w", encoding="utf-8") as f:
            json.dump({"viradas": viradas}, f)

    def _feed(self, snap):
        g.gerar_feed(CFG, snap)
        with open(os.path.join(g.DIR_SITE, "feed.xml"), encoding="utf-8") as f:
            return f.read()

    def _pubdates(self, xml):
        raiz = ET.fromstring(xml)
        return [i.findtext("pubDate") for i in raiz.findall(".//item")]

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

    def test_feed_pubdate_vem_do_historico(self):
        v = self.SNAP["viradas"][0]
        self._historico("2026-08-19.json", [v])
        self._historico("2026-08-20.json", [v])
        self._historico("2026-08-22.json", [v])
        xml = self._feed(self.SNAP)
        self.assertEqual(self._pubdates(xml), ["Wed, 19 Aug 2026 00:00:00 GMT"])
        # lastBuildDate continua sendo a data da coleta.
        self.assertIn("<lastBuildDate>Sat, 22 Aug 2026 00:00:00 GMT</lastBuildDate>", xml)

    def test_feed_pubdate_cai_para_vigencia_e_depois_para_a_coleta(self):
        v = dict(self.SNAP["viradas"][0], vigente_desde="2026-08-10")
        snap = dict(self.SNAP, viradas=[v])
        self.assertEqual(
            self._pubdates(self._feed(snap)), ["Mon, 10 Aug 2026 00:00:00 GMT"]
        )
        # vigente_desde vem cru do arquivo oficial: lixo nao vira pubDate.
        v["vigente_desde"] = "10/08/2026"
        self.assertEqual(
            self._pubdates(self._feed(snap)), ["Sat, 22 Aug 2026 00:00:00 GMT"]
        )
        # Um adiamento e uma chave nova: o historico da data antiga nao vale.
        self._historico("2026-08-01.json", [dict(v, vira_obrigatorio_em="2026-09-30")])
        self.assertEqual(
            self._pubdates(self._feed(snap)), ["Sat, 22 Aug 2026 00:00:00 GMT"]
        )


class TestLastmod(unittest.TestCase):
    def test_calcular_lastmod_mantem_data_quando_hash_igual(self):
        anterior = {
            "/": ["aaa", "2026-08-01"],
            "/ncm/1/": ["bbb", "2026-08-02"],
            "/sumiu/": ["ccc", "2026-08-03"],
            "/quebrado/": "lixo",
        }
        paginas = {"/": "aaa", "/ncm/1/": "novo", "/nova/": "ddd", "/quebrado/": "eee"}
        atual, mudadas = g.calcular_lastmod(anterior, paginas, "2026-08-22")
        self.assertEqual(
            atual,
            {
                "/": ["aaa", "2026-08-01"],
                "/ncm/1/": ["novo", "2026-08-22"],
                "/nova/": ["ddd", "2026-08-22"],
                "/quebrado/": ["eee", "2026-08-22"],
            },
        )
        self.assertEqual(sorted(mudadas), ["/ncm/1/", "/nova/", "/quebrado/"])
        # Pura: nao toca no mapa anterior.
        self.assertIn("/sumiu/", anterior)

    def test_assinatura_por_dados_ignora_a_ordem_das_chaves(self):
        a = g.assinatura_dados("t", "d", {"b": 1, "a": [{"y": 2, "x": 1}]})
        b = g.assinatura_dados("t", "d", {"a": [{"x": 1, "y": 2}], "b": 1})
        self.assertEqual(a, b)
        self.assertNotEqual(a, g.assinatura_dados("t", "d", {"a": [], "b": 1}))

    def test_virada_estavel_nao_carrega_nada_derivado_do_dia(self):
        v = {
            "ncm": "1",
            "atributo": "A",
            "nome": "N",
            "orgaos": ["X"],
            "vira_obrigatorio_em": "2099-01-01",
            "vigente_desde": "2020-01-01",
            "modalidade": "IMPORTACAO",
            "forma_preenchimento": "TEXTO",
        }
        self.assertEqual(
            set(g.virada_estavel(v)),
            {"ncm", "atributo", "nome", "orgaos", "vira_obrigatorio_em"},
        )


class TestSitemap(unittest.TestCase):
    SNAP = {"data_referencia": "2026-08-22", "contagens": {"versao": "1"}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.antigo = g.DIR_SITE
        g.DIR_SITE = self.tmp.name

    def tearDown(self):
        g.DIR_SITE = self.antigo
        self.tmp.cleanup()

    def _le(self, nome):
        with open(os.path.join(self.tmp.name, nome), encoding="utf-8") as f:
            return f.read()

    def test_sitemap_index_lastmod_e_o_maximo_do_bloco(self):
        caminhos = ["/", "/ncm/a/", "/ncm/b/", "/atributos/x/"]
        datas = {
            "/": ["h", "2026-08-01"],
            "/ncm/a/": ["h", "2026-08-10"],
            "/ncm/b/": ["h", "2026-08-05"],
        }
        g.gerar_sitemap(CFG, caminhos, self.SNAP, datas, ["/ncm/a/"])
        indice = ET.fromstring(self._le("sitemap.xml"))
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        por_arquivo = {
            e.findtext("s:loc", namespaces=ns).rsplit("/", 1)[1]: e.findtext(
                "s:lastmod", namespaces=ns
            )
            for e in indice.findall("s:sitemap", ns)
        }
        self.assertEqual(
            por_arquivo,
            {
                "sitemap-ncm.xml": "2026-08-10",
                "sitemap-geral.xml": "2026-08-01",
                # Sem registro no mapa, a URL leva a data de hoje.
                "sitemap-atributos.xml": "2026-08-22",
            },
        )
        self.assertEqual(self._le("mudancas.txt"), "https://exemplo.test/repo/ncm/a/\n")

    def test_rebuild_acima_do_teto_manda_uma_url_so(self):
        caminhos = [f"/ncm/{i}/" for i in range(g.TETO_INDEXNOW + 1)]
        g.gerar_sitemap(CFG, caminhos, self.SNAP, {}, list(caminhos))
        self.assertEqual(self._le("mudancas.txt").count("\n"), 1)


class TestPagina(unittest.TestCase):
    SNAP = {"data_referencia": "2026-08-22", "contagens": {"versao": "1"}}

    def test_sem_caminho_sai_noindex_e_sem_canonical(self):
        html = g.pagina(CFG, self.SNAP, "<p>x</p>", "T", "D", caminho=None)
        self.assertIn('<meta name="robots" content="noindex">', html)
        self.assertNotIn("canonical", html)
        self.assertNotIn("og:url", html)
        self.assertNotIn("{{", html)

    def test_com_caminho_sai_canonical_e_og_url(self):
        html = g.pagina(CFG, self.SNAP, "<p>x</p>", "T", "D", "/ncm/1/")
        self.assertIn(
            '<link rel="canonical" href="https://exemplo.test/repo/ncm/1/">', html
        )
        self.assertIn(
            '<meta property="og:url" content="https://exemplo.test/repo/ncm/1/">', html
        )
        self.assertNotIn("noindex", html)

    def test_goatcounter_por_https(self):
        bloco = g.bloco_analytics(dict(CFG, goatcounter_code="x"))
        self.assertIn('src="https://gc.zgo.at/count.js"', bloco)


class TestPng(unittest.TestCase):
    def test_assinatura_e_dimensoes(self):
        import struct

        d = g._png_solido(10, 4, [(0, 0, 5, 2, (255, 0, 0))], (0, 0, 0))
        self.assertEqual(d[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", d[16:24]), (10, 4))


if __name__ == "__main__":
    unittest.main()
