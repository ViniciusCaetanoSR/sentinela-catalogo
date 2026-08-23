"""Testes das funcoes puras do gerador. Sem rede e sem escrever no site/."""

import contextlib
import io
import json
import os
import re
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
SNAP = {"data_referencia": "2026-08-22", "contagens": {"versao": "1"}, "viradas": []}


def setUpModule():
    apoio.proibir_rede()


def _build(raiz, snapshot=None, ncms_com_pagina=(), cfg=CFG):
    """Um Build mínimo apontando para `raiz`. Os templates vêm do repositório
    real quando raiz é o padrão; os testes que escrevem usam um tmp."""
    return g.Build(
        cfg=cfg,
        caminhos=comum.Caminhos(raiz=raiz),
        snapshot=snapshot or SNAP,
        catalogo={"atributos": [], "orgaos": []},
        completo={"ncms": {}, "atributos": {}},
        ncms_com_pagina=set(ncms_com_pagina),
    )


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

    def test_grafo_tira_o_context_de_cada_no(self):
        trilha = g.trilha_dados(CFG, [("Início", "/")])
        pagina = g.pagina_dados(CFG, "/ncm/1/", "2026-08-01")
        grafo = g.grafo(trilha, pagina)
        self.assertEqual(grafo["@context"], "https://schema.org")
        self.assertEqual(
            [no["@type"] for no in grafo["@graph"]], ["BreadcrumbList", "WebPage"]
        )
        self.assertTrue(all("@context" not in no for no in grafo["@graph"]))
        self.assertEqual(
            pagina,
            {
                "@type": "WebPage",
                "url": "https://exemplo.test/repo/ncm/1/",
                "dateModified": "2026-08-01",
                "isPartOf": {"@type": "Dataset", "url": "https://exemplo.test/repo/"},
            },
        )
        # Puro: a trilha recebida continua com o seu @context.
        self.assertIn("@context", trilha)


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
        real = set(comum.carregar_config(comum.padrao().config))
        self.assertTrue(esperado <= real, f"faltam {esperado - real}")
        self.assertEqual(set(comum.CONFIG_PADRAO), esperado)
        readme = os.path.join(comum.RAIZ, "README.md")
        with open(readme, encoding="utf-8") as f:
            texto = f.read()
        for chave in esperado:
            self.assertIn(chave, texto, f"{chave} nao esta documentado no README")


class TestHistorico(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.build = _build(self.tmp.name)
        os.makedirs(self.build.caminhos.historico)

    def _grava(self, nome, corpo):
        caminho = os.path.join(self.build.caminhos.historico, nome)
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(corpo)

    def _bloco(self, ncms_com_pagina=()):
        self.build.ncms_com_pagina = set(ncms_com_pagina)
        return g.bloco_historico(self.build, date(2026, 8, 22))

    def test_sem_arquivo_suficiente(self):
        self.assertEqual(self._bloco(), "")

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
        html = self._bloco()
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
        # O arquivo de ontem foi ignorado (não tem "viradas"), então a
        # virada de hoje é nova - e nada levanta.
        html = self._bloco()
        self.assertIn("Viradas novas", html)
        self.assertIn("<li>1 — N, a partir de 01/01/2099</li>", html)

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
        self.assertEqual(self._bloco(), "")

    def test_fronteira_da_janela_de_30_dias(self):
        # Referência 22/08: 23/07 é o trigésimo dia e conta; 22/07 é o
        # trigésimo primeiro e fica de fora. A virada só existe no arquivo
        # antigo, então "saiu da lista" aparece se e só se ele entra.
        antigo = json.dumps({"viradas": [self._virada("1", "A", "2026-08-01", "Velha")]})
        self._grava("2026-08-22.json", json.dumps({"viradas": []}))
        self._grava("2026-07-23.json", antigo)
        self.assertIn("Velha", self._bloco())
        os.remove(os.path.join(self.build.caminhos.historico, "2026-07-23.json"))
        self._grava("2026-07-22.json", antigo)
        self.assertEqual(self._bloco(), "")
        arquivos = g.arquivos_historico(
            self.build.caminhos.historico, date(2026, 8, 22), g.JANELA_HISTORICO
        )
        self.assertEqual([q.isoformat() for q, _ in arquivos], ["2026-08-22"])

    def test_buraco_na_janela_nao_derruba(self):
        # Dez dias sem coleta entre um arquivo e o outro: compara com o
        # último que existe, sem inventar os que faltam.
        self._grava(
            "2026-08-10.json",
            json.dumps({"viradas": [self._virada("1", "A", "2026-09-01", "De antes")]}),
        )
        self._grava(
            "2026-08-22.json",
            json.dumps({"viradas": [self._virada("2", "B", "2026-09-01", "De hoje")]}),
        )
        html = self._bloco()
        self.assertIn("<li>2 — De hoje, a partir de 01/09/2026</li>", html)
        self.assertIn("<li>1 — De antes</li>", html)

    def test_nome_que_nao_e_data_e_ignorado(self):
        # Um .json perdido na pasta (notas, backup) não é snapshot: nem
        # conta como arquivo da janela nem contribui com viradas.
        self._grava("2026-08-22.json", json.dumps({"viradas": []}))
        for nome in (
            "notas.json",
            "2026-08-21-backup.json",
            "ultimo.json",
            "2026-08-21.txt",
        ):
            self._grava(
                nome,
                json.dumps({"viradas": [self._virada("9", "Z", "2026-09-01", "Lixo")]}),
            )
        self.assertEqual(self._bloco(), "")
        arquivos = g.arquivos_historico(self.build.caminhos.historico, date(2026, 8, 22))
        self.assertEqual([os.path.basename(c) for _, c in arquivos], ["2026-08-22.json"])

    def test_arquivo_do_futuro_e_ignorado(self):
        # Um snapshot datado depois da referência (relógio errado, fixture
        # de teste esquecida) não pode ser "hoje" nem entrar na comparação.
        self._dois_dias(
            [self._virada("1", "A", "2026-09-01")],
            [self._virada("1", "A", "2026-09-01")],
        )
        self._grava(
            "2026-08-23.json",
            json.dumps({"viradas": [self._virada("7", "F", "2026-09-01", "Do futuro")]}),
        )
        self.assertEqual(self._bloco(), "")
        vista = g.primeira_vista(self.build.caminhos.historico, date(2026, 8, 22))
        self.assertNotIn(("7", "F", "2026-09-01"), vista)

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
        html = self._bloco()
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
        html = self._bloco({"1", "3"})
        self.assertIn('<li><a href="/repo/ncm/1/">1</a> — Com pagina</li>', html)
        self.assertIn("<li>2 — Sem pagina</li>", html)
        self.assertIn(
            '<li><a href="/repo/ncm/3/">3</a> — Nova, a partir de 01/01/2099', html
        )
        # Sem o conjunto, nada vira link: o site tem de continuar fechado.
        self.assertNotIn("<a ", self._bloco())

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
            html = self._bloco()
        # O arquivo de ontem foi ignorado, entao a virada parece nova.
        self.assertIn("Viradas novas", html)
        self.assertIn("schema", erro.getvalue())
        self.assertIn("2026-08-21.json", erro.getvalue())

    def test_snapshot_schema_1_do_historico_continua_lido(self):
        # O histórico tem os dois formatos lado a lado: o 1 (nome e órgãos
        # dentro de cada virada, ficha das NCMs afetadas, sem a chave
        # "schema") e o 2 (mapa "atributos"). O bloco tem de cruzar os dois.
        self._grava(
            "2026-08-21.json",
            json.dumps(
                {
                    "viradas": [
                        {
                            "ncm": "1",
                            "atributo": "ATT_VELHO",
                            "nome": "Nome do formato antigo",
                            "orgaos": ["X"],
                            "forma_preenchimento": "TEXTO",
                            "vira_obrigatorio_em": "2026-08-21",
                        }
                    ],
                    "ncms_afetadas": [{"ncm": "1", "atributos": []}],
                }
            ),
        )
        self._grava(
            "2026-08-22.json",
            json.dumps(
                {
                    "schema": 2,
                    "viradas": [
                        {
                            "ncm": "2",
                            "atributo": "ATT_NOVO",
                            "vira_obrigatorio_em": "2099-01-01",
                        }
                    ],
                    "atributos": {"ATT_NOVO": {"nome": "Nome do mapa", "orgaos": ["Y"]}},
                }
            ),
        )
        html = self._bloco()
        self.assertIn("Nome do formato antigo", html)
        self.assertIn("Nome do mapa, a partir de 01/01/2099", html)
        self.assertNotIn("ATT_NOVO", html)

    def _virada(self, ncm, atributo, data, nome="N"):
        return {"ncm": ncm, "atributo": atributo, "nome": nome, "vira_obrigatorio_em": data}

    def _dois_dias(self, ontem, hoje):
        self._grava("2026-08-21.json", json.dumps({"viradas": ontem}))
        self._grava("2026-08-22.json", json.dumps({"viradas": hoje}))

    def test_adiamento_aparece_em_prazos_alterados(self):
        self._dois_dias(
            [self._virada("1", "A", "2026-09-01", "Com pagina")],
            [self._virada("1", "A", "2026-10-01", "Com pagina")],
        )
        html = self._bloco({"1"})
        self.assertIn("<h3>Prazos alterados</h3>", html)
        self.assertIn(
            '<li><a href="/repo/ncm/1/">1</a> — Com pagina: de 01/09/2026 para '
            "01/10/2026</li>",
            html,
        )
        # O par não é nem novo nem sumido: só mudou de data.
        self.assertNotIn("Viradas novas", html)
        self.assertNotIn("Saíram da lista", html)
        # Sem a página da NCM, texto - o site tem de continuar fechado.
        self.assertIn(
            "<li>1 — Com pagina: de 01/09/2026 para 01/10/2026</li>", self._bloco()
        )

    def test_antecipacao_tambem(self):
        self._dois_dias(
            [self._virada("1", "A", "2026-10-01")],
            [self._virada("1", "A", "2026-09-01")],
        )
        html = self._bloco()
        self.assertIn("<h3>Prazos alterados</h3>", html)
        self.assertIn("<li>1 — N: de 01/10/2026 para 01/09/2026</li>", html)

    def test_sem_alteracao_nao_emite_secao(self):
        self._dois_dias(
            [self._virada("1", "A", "2026-09-01")],
            [self._virada("1", "A", "2026-09-01")],
        )
        self.assertEqual(self._bloco(), "")
        # Com uma virada nova ao lado, a seção de prazos continua ausente.
        self._grava(
            "2026-08-22.json",
            json.dumps(
                {
                    "viradas": [
                        self._virada("1", "A", "2026-09-01"),
                        self._virada("2", "B", "2099-01-01"),
                    ]
                }
            ),
        )
        html = self._bloco()
        self.assertIn("Viradas novas", html)
        self.assertNotIn("Prazos alterados", html)

    def test_prazo_alterado_compara_com_a_ultima_data_vista(self):
        # O adiamento foi notícia no dia 21 (de 09 para 10). No dia 22 a data
        # é a mesma do dia 21: não há nada novo a dizer, mesmo que o dia 20
        # ainda esteja na janela com a data antiga.
        self._grava(
            "2026-08-20.json",
            json.dumps({"viradas": [self._virada("1", "A", "2026-09-01")]}),
        )
        self._dois_dias(
            [self._virada("1", "A", "2026-10-01")],
            [self._virada("1", "A", "2026-10-01")],
        )
        self.assertEqual(self._bloco(), "")

    def test_prazos_alterados_em_lote(self):
        # Um lote adiado de uma vez é UM item, como na home e no feed - e o
        # nome vira link quando o atributo tem página.
        n = g.LIMIAR_LOTE + 1
        ontem = [
            self._virada(f"9001.{i:02d}.00", "ATT_L", "2027-01-01", "Lote")
            for i in range(n)
        ]
        hoje = [dict(v, vira_obrigatorio_em="2027-07-01") for v in ontem]
        self._dois_dias(ontem, hoje)
        self.build.com_pagina = {"ATT_L"}
        html = self._bloco()
        self.assertIn(
            f'<li><a href="/repo/atributos/ATT_L/">Lote</a> de 01/01/2027 para '
            f"01/07/2027 em {n} NCMs</li>",
            html,
        )
        self.assertEqual(html.count("<li>"), 1)

    def test_data_ausente_nao_e_alteracao(self):
        self._dois_dias(
            [{"ncm": "1", "atributo": "A", "nome": "N"}],
            [self._virada("1", "A", "2026-09-01")],
        )
        self.assertEqual(self._bloco(), "")

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
        vista = g.primeira_vista(self.build.caminhos.historico, date(2026, 8, 22))
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
        self.addCleanup(self.tmp.cleanup)
        self.caminhos = comum.Caminhos(raiz=self.tmp.name)
        os.makedirs(self.caminhos.historico)

    def _historico(self, nome, viradas):
        with open(os.path.join(self.caminhos.historico, nome), "w", encoding="utf-8") as f:
            json.dump({"viradas": viradas}, f)

    def _feed(self, snap):
        g.gerar_feed(_build(self.tmp.name, snapshot=snap))
        with open(os.path.join(self.caminhos.site, "feed.xml"), encoding="utf-8") as f:
            return f.read()

    def _pubdates(self, xml):
        raiz = ET.fromstring(xml)
        return [i.findtext("pubDate") for i in raiz.findall(".//item")]

    def test_xml_valido(self):
        raiz = ET.fromstring(self._feed(self.SNAP))
        self.assertEqual(raiz.tag, "rss")
        self.assertEqual(len(raiz.findall(".//item")), 1)

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

    def test_feed_agrupa_lote_num_item_so(self):
        # 10 mil itens iguais a menos da NCM não são notícia: o lote vira um
        # item, com link para a página do atributo e guid atributo-data.
        lote = [
            dict(self.SNAP["viradas"][0], ncm=f"{i:04d}.00.00")
            for i in range(g.LIMIAR_LOTE + 1)
        ]
        solta = dict(self.SNAP["viradas"][0], atributo="ATT_2", nome="Solta")
        snap = dict(self.SNAP, viradas=lote + [solta])
        self._historico("2026-08-10.json", [lote[3]])
        xml = self._feed(snap)
        raiz = ET.fromstring(xml)
        itens = raiz.findall(".//item")
        self.assertEqual(len(itens), 2)
        self.assertEqual(
            itens[0].findtext("title"), "Teste vira obrigatório em 30/08/2026 para 51 NCMs"
        )
        self.assertEqual(
            itens[0].findtext("link"), "https://exemplo.test/repo/atributos/ATT_1/"
        )
        self.assertEqual(itens[0].findtext("guid"), "ATT_1-2026-08-30")
        # O pubDate do lote é o da NCM vista há mais tempo.
        self.assertEqual(itens[0].findtext("pubDate"), "Mon, 10 Aug 2026 00:00:00 GMT")
        self.assertIn("NCM 8415.10.90: Solta", itens[1].findtext("title"))


def _viradas(n, atributo="ATT_1", data="2026-08-30", nome="Nome"):
    return [
        {
            "ncm": f"{i:04d}.00.00",
            "atributo": atributo,
            "nome": nome,
            "orgaos": ["X"],
            "vira_obrigatorio_em": data,
        }
        for i in range(n)
    ]


class TestLotes(unittest.TestCase):
    def test_agrupa_so_acima_do_limiar(self):
        # Exatamente o limiar continua solto; um a mais vira lote.
        no_limiar = _viradas(g.LIMIAR_LOTE)
        lotes, soltas = g.agrupar_viradas(no_limiar)
        self.assertEqual((lotes, soltas), ([], no_limiar))
        acima = _viradas(g.LIMIAR_LOTE + 1)
        lotes, soltas = g.agrupar_viradas(acima)
        self.assertEqual(soltas, [])
        self.assertEqual(len(lotes), 1)
        self.assertEqual(lotes[0]["atributo"], "ATT_1")
        self.assertEqual(lotes[0]["nome"], "Nome")
        self.assertEqual(lotes[0]["orgaos"], ["X"])
        self.assertEqual(lotes[0]["data"], "2026-08-30")
        self.assertEqual(lotes[0]["ncms"], [v["ncm"] for v in acima])

    def test_lote_e_por_atributo_e_data(self):
        # O mesmo atributo em duas datas são dois grupos: um pode ser lote e
        # o outro não.
        a = _viradas(3, data="2026-09-01")
        b = _viradas(3, data="2026-10-01")
        outro = _viradas(3, atributo="ATT_2")
        lotes, soltas = g.agrupar_viradas(a + b + outro, limiar=2)
        self.assertEqual(
            [(lote["data"], lote["atributo"]) for lote in lotes],
            [("2026-08-30", "ATT_2"), ("2026-09-01", "ATT_1"), ("2026-10-01", "ATT_1")],
        )
        self.assertEqual(soltas, [])
        lotes, soltas = g.agrupar_viradas(a + b[:2] + outro, limiar=2)
        self.assertEqual(len(lotes), 2)
        # As soltas mantêm a ordem recebida.
        self.assertEqual(soltas, b[:2])

    def test_frase_do_lote(self):
        lote = g.agrupar_viradas(_viradas(1234), limiar=1)[0][0]
        self.assertEqual(
            g.frase_lote(lote), "Nome vira obrigatório em 30/08/2026 para 1.234 NCMs"
        )
        lote["nome"] = None
        self.assertTrue(g.frase_lote(lote).startswith("ATT_1 vira"))

    def test_tabela_da_home_tem_uma_linha_por_lote_e_o_teto_de_soltas(self):
        build = _build(comum.RAIZ)
        lotes, soltas = g.agrupar_viradas(_viradas(3) + _viradas(3, atributo="ATT_2"), 5)
        self.assertEqual(lotes, [])
        with mock.patch.object(g, "TETO_LINHAS_HOME", 4):
            html = g.tabela_viradas(build, [], soltas, date(2026, 8, 22))
        self.assertEqual(html.count('<td role="cell" class="ncm"'), 4)
        self.assertIn("mostra as 4 primeiras de 6 viradas", html)
        self.assertIn('href="/repo/atributos/"', html)
        lotes, soltas = g.agrupar_viradas(_viradas(3) + _viradas(1, atributo="ATT_2"), 2)
        html = g.tabela_viradas(build, lotes, soltas, date(2026, 8, 22))
        self.assertEqual(html.count('<tr role="row" class="lote">'), 1)
        self.assertEqual(html.count('<td role="cell" class="ncm"'), 1)
        self.assertIn("<strong>3 NCMs</strong>", html)
        self.assertIn(
            '<a href="/repo/atributos/ATT_1/">veja as NCMs na página do atributo</a>', html
        )
        self.assertNotIn("mostra as", html)


class TestNormalizarSnapshot(unittest.TestCase):
    def test_schema_2_completa_as_viradas_pelo_mapa(self):
        snap = {
            "schema": 2,
            "viradas": [
                {"ncm": "1", "atributo": "A", "vira_obrigatorio_em": "2099-01-01"},
                {"ncm": "2", "atributo": "B", "vira_obrigatorio_em": "2099-01-01"},
            ],
            "atributos": {
                "A": {"nome": "Nome A", "orgaos": ["X"], "forma_preenchimento": "TEXTO"}
            },
        }
        novo = g.normalizar_snapshot(snap)
        self.assertEqual(
            novo["viradas"][0],
            {
                "ncm": "1",
                "atributo": "A",
                "vira_obrigatorio_em": "2099-01-01",
                "nome": "Nome A",
                "orgaos": ["X"],
                "forma_preenchimento": "TEXTO",
            },
        )
        # Atributo fora do mapa: as chaves existem, vazias - nunca KeyError.
        self.assertEqual(novo["viradas"][1]["nome"], None)
        self.assertEqual(novo["viradas"][1]["orgaos"], [])
        # Cópia: o dicionário recebido fica como estava.
        self.assertNotIn("nome", snap["viradas"][0])

    def test_schema_1_passa_inteiro_e_perde_a_ficha(self):
        v = {
            "ncm": "1",
            "atributo": "A",
            "nome": "N",
            "orgaos": ["X"],
            "vira_obrigatorio_em": "x",
        }
        snap = {"viradas": [v], "ncms_afetadas": [{"ncm": "1"}]}
        novo = g.normalizar_snapshot(snap)
        self.assertEqual(novo["viradas"][0]["nome"], "N")
        self.assertIn("forma_preenchimento", novo["viradas"][0])
        self.assertNotIn("ncms_afetadas", novo)
        self.assertIn("ncms_afetadas", snap)

    def test_schema_de(self):
        self.assertEqual(g.schema_de({}), 1)
        self.assertEqual(g.schema_de({"schema": 2}), 2)
        self.assertTrue(g.schema_legivel({"schema": g.SCHEMA_SUPORTADO}))
        self.assertFalse(g.schema_legivel({"schema": g.SCHEMA_SUPORTADO + 1}))
        self.assertFalse(g.schema_legivel({"schema": "2"}))


class TestLastmod(unittest.TestCase):
    MARCA = "hash-dos-templates"

    def test_calcular_lastmod_mantem_data_quando_hash_igual(self):
        anterior = {
            g.CHAVE_TEMPLATES: self.MARCA,
            "/": ["aaa", "2026-08-01"],
            "/ncm/1/": ["bbb", "2026-08-02"],
            "/sumiu/": ["ccc", "2026-08-03"],
            "/quebrado/": "lixo",
        }
        paginas = {"/": "aaa", "/ncm/1/": "novo", "/nova/": "ddd", "/quebrado/": "eee"}
        atual, mudadas, rebuild = g.calcular_lastmod(
            anterior, paginas, "2026-08-22", self.MARCA
        )
        self.assertEqual(
            atual,
            {
                g.CHAVE_TEMPLATES: self.MARCA,
                "/": ["aaa", "2026-08-01"],
                "/ncm/1/": ["novo", "2026-08-22"],
                "/nova/": ["ddd", "2026-08-22"],
                "/quebrado/": ["eee", "2026-08-22"],
            },
        )
        self.assertEqual(sorted(mudadas), ["/ncm/1/", "/nova/", "/quebrado/"])
        self.assertFalse(rebuild)
        # Pura: nao toca no mapa anterior.
        self.assertIn("/sumiu/", anterior)

    def test_rebuild_sem_lastmod_anterior_ou_com_templates_diferentes(self):
        paginas = {"/": "aaa"}
        anterior = {g.CHAVE_TEMPLATES: self.MARCA, "/": ["aaa", "2026-08-01"]}
        _, _, rebuild = g.calcular_lastmod({}, paginas, "2026-08-22", self.MARCA)
        self.assertTrue(rebuild)
        _, _, rebuild = g.calcular_lastmod(anterior, paginas, "2026-08-22", "outro")
        self.assertTrue(rebuild)
        # lastmod.json de antes da chave existir: rebuild, uma vez só.
        _, _, rebuild = g.calcular_lastmod(
            {"/": ["aaa", "2026-08-01"]}, paginas, "2026-08-22", self.MARCA
        )
        self.assertTrue(rebuild)
        # Num rebuild por template, a data das páginas cujo dado não mudou
        # fica: o lastmod continua honesto.
        atual, mudadas, rebuild = g.calcular_lastmod(
            anterior, paginas, "2026-08-22", "outro"
        )
        self.assertEqual(atual["/"], ["aaa", "2026-08-01"])
        self.assertEqual(mudadas, [])

    def test_data_modificacao_concorda_com_calcular_lastmod(self):
        # O JSON-LD da pagina e escrito ANTES de calcular_lastmod rodar; os
        # dois aplicam a mesma regra (registro_vigente), e tem de dar a
        # mesma data em todos os casos: hash igual, hash diferente, pagina
        # nova, registro malformado.
        anterior = {
            g.CHAVE_TEMPLATES: self.MARCA,
            "/ncm/1/": ["aaa", "2026-08-01"],
            "/ncm/2/": ["bbb", "2026-08-02"],
            "/quebrado/": "lixo",
            "/curto/": ["ccc"],
        }
        paginas = {"/ncm/1/": "aaa", "/ncm/2/": "novo", "/nova/": "ddd", "/quebrado/": "e"}
        paginas["/curto/"] = "ccc"
        build = _build(comum.RAIZ)
        build.lastmod_anterior = anterior
        atual, _, _ = g.calcular_lastmod(anterior, paginas, "2026-08-22", self.MARCA)
        for caminho, marca in paginas.items():
            self.assertEqual(
                g.data_modificacao(build, caminho, marca), atual[caminho][1], caminho
            )
        self.assertEqual(g.data_modificacao(build, "/ncm/1/", "aaa"), "2026-08-01")
        self.assertEqual(g.data_modificacao(build, "/ncm/2/", "novo"), "2026-08-22")
        # Sem lastmod anterior (primeiro build), tudo e de hoje.
        build.lastmod_anterior = {}
        self.assertEqual(g.data_modificacao(build, "/ncm/1/", "aaa"), "2026-08-22")

    def test_chave_templates_nao_entra_nas_mudadas(self):
        paginas = {"/": "aaa", "__templates__": "zzz", "__outra__": "y"}
        atual, mudadas, _ = g.calcular_lastmod({}, paginas, "2026-08-22", self.MARCA)
        self.assertEqual(mudadas, ["/"])
        self.assertEqual(atual[g.CHAVE_TEMPLATES], self.MARCA)
        self.assertNotIn("__outra__", atual)

    def test_hash_templates_muda_com_o_conteudo(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminhos = comum.Caminhos(raiz=tmp)
            os.makedirs(caminhos.templates)
            os.makedirs(caminhos.fontes)
            with open(os.path.join(caminhos.templates, "a.html"), "w") as f:
                f.write("a")
            antes = g.hash_templates(caminhos)
            self.assertEqual(antes, g.hash_templates(caminhos))
            with open(os.path.join(caminhos.fontes, "fontes.css"), "w") as f:
                f.write("b")
            depois = g.hash_templates(caminhos)
            self.assertNotEqual(antes, depois)
            # Um arquivo de fontes que não é o fontes.css não entra.
            with open(os.path.join(caminhos.fontes, "x.woff2"), "wb") as f:
                f.write(b"\x00")
            self.assertEqual(depois, g.hash_templates(caminhos))

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
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.build = _build(self.tmp.name)

    def _le(self, nome):
        with open(os.path.join(self.build.caminhos.site, nome), encoding="utf-8") as f:
            return f.read()

    def test_sitemap_index_lastmod_e_o_maximo_do_bloco(self):
        caminhos = ["/", "/ncm/a/", "/ncm/b/", "/atributos/x/"]
        datas = {
            "/": ["h", "2026-08-01"],
            "/ncm/a/": ["h", "2026-08-10"],
            "/ncm/b/": ["h", "2026-08-05"],
        }
        g.gerar_sitemap(self.build, caminhos, datas, ["/ncm/a/"])
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

    def test_rebuild_por_template_manda_so_a_raiz(self):
        caminhos = [f"/ncm/{i}/" for i in range(5)]
        g.gerar_sitemap(self.build, caminhos, {}, caminhos[:2], rebuild=True)
        self.assertEqual(self._le("mudancas.txt"), "https://exemplo.test/repo/\n")

    def test_mudancas_sem_teto_fora_de_rebuild(self):
        # Uma virada em massa muda 10 mil páginas de verdade: vai tudo, e
        # indexnow.py divide em lotes. O teto de 200 URLs não existe mais.
        caminhos = [f"/ncm/{i:05d}/" for i in range(1000)]
        g.gerar_sitemap(self.build, caminhos, {}, list(caminhos))
        linhas = self._le("mudancas.txt").splitlines()
        self.assertEqual(len(linhas), 1000)
        self.assertEqual(linhas[0], "https://exemplo.test/repo/ncm/00000/")

    def test_chave_templates_nao_vira_url(self):
        caminhos = ["/", "/ncm/a/", g.CHAVE_TEMPLATES]
        datas = {g.CHAVE_TEMPLATES: "abc", "/": ["h", "2026-08-01"]}
        g.gerar_sitemap(self.build, caminhos, datas, list(caminhos))
        for nome in os.listdir(self.build.caminhos.site):
            if nome.endswith(".xml") or nome == "mudancas.txt":
                self.assertNotIn("__", self._le(nome), nome)
        self.assertEqual(len(self._le("mudancas.txt").splitlines()), 2)


class TestPagina(unittest.TestCase):
    # Os templates reais, sem escrever nada: pagina() só monta a string.
    BUILD = _build(comum.RAIZ)

    def test_sem_caminho_sai_noindex_e_sem_canonical(self):
        html = g.pagina(self.BUILD, "<p>x</p>", "T", "D", caminho=None)
        self.assertIn('<meta name="robots" content="noindex">', html)
        self.assertNotIn("canonical", html)
        self.assertNotIn("og:url", html)
        self.assertNotIn("{{", html)

    def test_com_caminho_sai_canonical_e_og_url(self):
        html = g.pagina(self.BUILD, "<p>x</p>", "T", "D", "/ncm/1/")
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


def _app_js():
    with open(os.path.join(comum.padrao().templates, "app.js"), encoding="utf-8") as f:
        return f.read()


class TestPrazo(unittest.TestCase):
    # (dias, curto, frase, contagem, unidade): a tabela de casos que o JS
    # tem de reproduzir. -3 e -1 só acontecem no navegador, com dado velho.
    CASOS = [
        (-3, "prazo vencido há 3 dias", "Prazo vencido há 3 dias."),
        (-1, "prazo vencido há 1 dia", "Prazo vencido há 1 dia."),
        (0, "hoje", "É hoje."),
        (1, "amanhã", "Falta 1 dia."),
        (2, "em 2 dias", "Faltam 2 dias."),
        (7, "em 7 dias", "Faltam 7 dias."),
        (30, "em 30 dias", "Faltam 30 dias."),
    ]

    def test_prazo_humano_tabela_de_casos(self):
        for dias, curto, frase in self.CASOS:
            with self.subTest(dias=dias):
                self.assertEqual(g.prazo_humano(dias), curto)
                self.assertEqual(g.prazo_humano(dias, "frase"), frase)

    def test_prazo_humano_contagem_e_unidade(self):
        self.assertEqual(
            g.prazo_humano(0, "contagem", "22/08/2026"),
            "O próximo corte é hoje, 22/08/2026.",
        )
        self.assertEqual(
            g.prazo_humano(1, "contagem", "23/08/2026"),
            "Falta 1 dia para o próximo corte, em 23/08/2026.",
        )
        self.assertEqual(
            g.prazo_humano(8, "contagem", "30/08/2026"),
            "Faltam 8 dias para o próximo corte, em 30/08/2026.",
        )
        self.assertEqual(
            g.prazo_humano(-2, "contagem", "20/08/2026"),
            "O próximo corte foi em 20/08/2026, há 2 dias.",
        )
        self.assertEqual(
            [g.prazo_humano(d, "unidade") for d in (-2, -1, 0, 1, 2)],
            ["dias atrás", "dia atrás", "dias", "dia", "dias"],
        )
        self.assertEqual(
            [g.prazo_humano(d, "h1") for d in (-2, 0, 1, 8)],
            ["há 2 dias", "hoje", "amanhã", "nos próximos 8 dias"],
        )

    def test_largura_da_barra(self):
        # 30 dias enche; hoje e vencido ficam no mínimo visível.
        self.assertEqual(g.largura_prazo(30), 100)
        self.assertEqual(g.largura_prazo(60), 100)
        self.assertEqual(g.largura_prazo(15), 50)
        self.assertEqual(g.largura_prazo(0), 6)
        self.assertEqual(g.largura_prazo(-5), 6)

    def test_textos_de_prazo_iguais_no_js(self):
        # O app.js reescreve os prazos no navegador com uma cópia desta
        # tabela. Sem Node na suíte, o que dá para garantir é que a cópia é
        # literalmente igual: o bloco é JSON estrito e sai daqui por
        # json.loads. Os dois números da barra e da urgência também.
        js = _app_js()
        m = re.search(r"var TEXTOS_PRAZO = (\{.*?\n  \});", js, re.S)
        self.assertIsNotNone(m, "TEXTOS_PRAZO não encontrado no app.js")
        tabela = json.loads(m.group(1))
        self.assertEqual(tabela, g.TEXTOS_PRAZO)
        m = re.search(r"var DIAS_BARRA_CHEIA = (\d+), DIAS_URGENTE = (\d+);", js)
        self.assertIsNotNone(m)
        self.assertEqual(
            (int(m.group(1)), int(m.group(2))), (g.DIAS_BARRA_CHEIA, g.DIAS_URGENTE)
        )
        # E a tabela de casos, aplicada à cópia do JS do mesmo jeito que o
        # script aplica (replace de {n} e {dia}), dá o mesmo texto.
        for dias, curto, frase in self.CASOS:
            n = abs(dias)
            caso = g.caso_prazo(dias)
            for estilo, esperado in (("curto", curto), ("frase", frase)):
                texto = (
                    tabela[estilo][caso]
                    .replace("{n}", str(n))
                    .replace("{dia}", g.plural(n, "dia", "dias"))
                )
                self.assertEqual(texto, esperado, (dias, estilo))

    def test_js_avisa_dado_velho_e_o_css_veste(self):
        self.assertIn('faixa.className = "dado-velho"', _app_js())
        self.assertIn("Estes dados são de", _app_js())
        self.assertIn('"data-referencia"', _app_js())
        with open(
            os.path.join(comum.padrao().templates, "estilo.css"), encoding="utf-8"
        ) as f:
            self.assertIn(".dado-velho{", f.read())

    def test_celula_da_tabela_carrega_data_corte_e_prazo_vencido(self):
        build = _build(comum.RAIZ)

        def celulas(referencia):
            return "".join(
                g._celulas_virada(build, "ATT_1", "Nome", ["X"], "2026-08-30", referencia)
            )

        html = celulas(date(2026, 8, 22))
        self.assertIn(
            '<time datetime="2026-08-30">30/08/2026</time>'
            '<br><span class="prazo-txt" data-corte="2026-08-30">em 8 dias</span>'
            '<span class="prazo"><i style="--w:27%"></i></span>',
            html,
        )
        html = celulas(date(2026, 8, 28))
        self.assertIn(
            '<span class="prazo-txt urgente" data-corte="2026-08-30">em 2 dias', html
        )
        # O build nunca produz prazo vencido (a regra é fim >= hoje), mas a
        # função é a mesma que o JS espelha e tem de saber escrevê-lo.
        html = celulas(date(2026, 9, 2))
        self.assertIn("prazo vencido há 3 dias</span>", html)
        self.assertIn('<span class="prazo urgente"><i style="--w:6%">', html)


class TestTabela(unittest.TestCase):
    def test_celula_com_e_sem_rotulo(self):
        self.assertEqual(
            g.celula("x", "Órgão", "num"),
            '<td role="cell" class="num" data-rot="Órgão">x</td>',
        )
        # Sem rótulo o data-rot não sai: no cartão mobile um rótulo seguido
        # de nada é uma pergunta sem resposta.
        self.assertEqual(g.celula(""), '<td role="cell"></td>')
        self.assertEqual(g.celula("x", 'a"b'), '<td role="cell" data-rot="a&quot;b">x</td>')

    def test_cabecalho_visivel_e_oculto(self):
        self.assertEqual(g.cabecalho("NCM"), '<th scope="col" role="columnheader">NCM</th>')
        self.assertEqual(
            g.cabecalho("NCMs", "num"),
            '<th scope="col" role="columnheader" class="num">NCMs</th>',
        )
        self.assertEqual(
            g.cabecalho("Situação", oculto=True),
            '<th scope="col" role="columnheader"><span class="oculto">Situação</span></th>',
        )

    def test_tabela_inteira_com_roles(self):
        html = g.tabela(
            'Viradas "x"',
            "Legenda <y>",
            [g.cabecalho("A")],
            [g.linha([g.celula("1", "A")], "lote"), g.linha([g.celula("2", "A")])],
        )
        self.assertEqual(
            html,
            '<div class="rolagem" tabindex="0" role="region" '
            'aria-label="Viradas &quot;x&quot;">'
            '<table role="table"><caption>Legenda &lt;y&gt;</caption>'
            '<thead role="rowgroup"><tr role="row">'
            '<th scope="col" role="columnheader">A</th></tr></thead>'
            '<tbody role="rowgroup">'
            '<tr role="row" class="lote"><td role="cell" data-rot="A">1</td></tr>'
            '<tr role="row"><td role="cell" data-rot="A">2</td></tr>'
            "</tbody></table></div>",
        )

    def test_data_html(self):
        self.assertEqual(
            g.data_html("2026-08-30"), '<time datetime="2026-08-30">30/08/2026</time>'
        )
        self.assertEqual(g.data_html(""), "")
        self.assertEqual(g.data_html(None), "")


class TestDadosAbertos(unittest.TestCase):
    def test_csv_escapa_virgula_e_aspas(self):
        viradas = [
            {
                "ncm": "1",
                "atributo": "A",
                "nome": 'Nome, com "aspas"',
                "orgaos": ["X", "Y"],
                "vira_obrigatorio_em": "2099-01-01",
                "vigente_desde": None,
                "modalidade": "IMPORTACAO",
            }
        ]
        texto = g.csv_viradas(viradas)
        self.assertEqual(
            texto,
            "ncm,atributo,nome,orgaos,vira_obrigatorio_em,vigente_desde,modalidade\n"
            '1,A,"Nome, com ""aspas""",X/Y,2099-01-01,,IMPORTACAO\n',
        )
        self.assertEqual(g.csv_viradas([]).count("\n"), 1)

    def test_snapshot_publico_tira_os_volateis(self):
        snap = {
            "schema": 2,
            "coletado_em": "2026-08-22T06:00:00-03:00",
            "data_referencia": "2026-08-22",
            "http": {"status": 200, "bytes_zip": 1, "sha256_json": "abc"},
            "bruto_novo": True,
            "viradas": [],
        }
        publico = g.snapshot_publico(snap)
        self.assertEqual(
            publico,
            {
                "schema": 2,
                "data_referencia": "2026-08-22",
                "http": {"status": 200, "sha256_json": "abc"},
                "viradas": [],
            },
        )
        # Puro: o snapshot do build continua inteiro.
        self.assertIn("coletado_em", snap)
        self.assertIn("bytes_zip", snap["http"])

    def test_linha_dados_abertos_e_distribuicoes(self):
        self.assertEqual(
            g.linha_dados_abertos(CFG),
            '<p class="dados-abertos">Dados abertos: '
            '<a href="/repo/dados/viradas.json">JSON</a> · '
            '<a href="/repo/dados/viradas.csv">CSV</a></p>',
        )
        self.assertEqual(
            [f for f, _ in g.DISTRIBUICOES],
            ["application/rss+xml", "application/json", "text/csv"],
        )


class TestEstaticos(unittest.TestCase):
    def test_css_fontes_reescreve_o_prefixo(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminhos = comum.Caminhos(raiz=tmp)
            build = _build(tmp)
            # Sem a pasta: vazio, e o site cai nas fontes do sistema.
            self.assertEqual(g.css_fontes(build), "")
            os.makedirs(caminhos.fontes)
            with open(os.path.join(caminhos.fontes, "fontes.css"), "w") as f:
                f.write("@font-face{src:url(/fontes/a.woff2)}")
            self.assertEqual(
                g.css_fontes(build), "@font-face{src:url(/repo/fontes/a.woff2)}"
            )
            build.cfg = SEM_PREFIXO
            self.assertEqual(g.css_fontes(build), "@font-face{src:url(/fontes/a.woff2)}")


class TestBuscaNcm(unittest.TestCase):
    def test_so_digitos(self):
        self.assertEqual(g.so_digitos("8415.10.90"), "84151090")
        self.assertEqual(g.so_digitos("8415 10 90"), "84151090")
        self.assertEqual(g.so_digitos(""), "")
        self.assertEqual(g.so_digitos(None), "")

    def test_form_usa_o_prefixo_e_cai_no_indice_sem_js(self):
        html = g.form_ncm(CFG)
        self.assertTrue(
            html.startswith(
                '<form class="ir-ncm" action="/repo/ncm/" method="get" role="search">'
            )
        )
        self.assertIn('<input id="ir-ncm-campo" name="ncm" inputmode="numeric"', html)
        self.assertIn('pattern="[0-9. ]{4,10}"', html)
        self.assertIn('<p class="ir-ncm-erro" role="alert"></p>', html)
        self.assertNotIn("data-404", html)
        self.assertIn('action="/ncm/"', g.form_ncm(SEM_PREFIXO))

    def test_form_da_404_e_marcado_e_escapa_o_valor(self):
        html = g.form_ncm(CFG, valor='"><x', na_404=True)
        self.assertIn('role="search" data-404="1">', html)
        self.assertIn('value="&quot;&gt;&lt;x"', html)


if __name__ == "__main__":
    unittest.main()
