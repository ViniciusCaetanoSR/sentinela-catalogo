"""Testes do coletor. Sem rede: o ZIP é montado em memória."""

import contextlib
import http.client
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from unittest import mock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apoio  # noqa: E402
import coletor  # noqa: E402

# A mesma data que a fixture usa em ATT_HOJE, para exercitar a fronteira.
HOJE = date(2026, 8, 22)
amostra = apoio.amostra


def setUpModule():
    apoio.proibir_rede()


class TestFimVigencia(unittest.TestCase):
    """As duas convenções de ausência, no mesmo arquivo."""

    def test_chave_omitida(self):
        self.assertIsNone(coletor._fim_vigencia({}))

    def test_string_vazia(self):
        # "" < qualquer data é True: comparar isso como data inverte o filtro.
        self.assertIsNone(coletor._fim_vigencia({"dataFimVigencia": ""}))

    def test_data_valida(self):
        self.assertEqual(
            coletor._fim_vigencia({"dataFimVigencia": "2026-08-30"}), "2026-08-30"
        )

    def test_data_com_formato_certo_mas_inexistente(self):
        # O regex antigo aceitava 30 de fevereiro; só quebrava no gerador,
        # com o dado já gravado e commitado. Agora é tratada como ausente e
        # contada à parte - um valor errado não custa mais o dia.
        self.assertIsNone(coletor._fim_vigencia({"dataFimVigencia": "2026-02-30"}))
        with self.assertRaises(ValueError):
            coletor.normalizar_data("2026-02-30")

    def test_formato_brasileiro(self):
        self.assertIsNone(coletor._fim_vigencia({"dataFimVigencia": "30/08/2026"}))
        with self.assertRaises(ValueError):
            coletor.normalizar_data("30/08/2026")

    def test_formato_compacto_e_recusado(self):
        # date.fromisoformat aceita "20260830" a partir do 3.11; o gerador
        # compara strings e esse valor nunca seria >= "2026-08-22".
        with self.assertRaises(ValueError):
            coletor.normalizar_data("20260830")

    def test_valor_que_nao_e_string(self):
        self.assertIsNone(coletor._fim_vigencia({"dataFimVigencia": 20260830}))


class TestViradas(unittest.TestCase):
    def setUp(self):
        self.dados = amostra()
        self.vs = coletor.viradas(self.dados, HOJE)
        self.codigos = [v["atributo"] for v in self.vs]

    def test_pega_o_futuro(self):
        self.assertIn("ATT_FUTURO", self.codigos)

    def test_inclui_o_dia_exato_da_virada(self):
        # A fronteira. Com o corte antigo (fim <= ref) o vínculo sumia
        # justamente no dia mais acionável, e a cópia "É hoje." nunca rodava.
        self.assertIn("ATT_HOJE", self.codigos)

    def test_ignora_data_passada(self):
        self.assertNotIn("ATT_PASSADO", self.codigos)

    def test_ignora_quem_ja_e_obrigatorio(self):
        self.assertNotIn("ATT_OBRIGATORIO_FUTURO", self.codigos)

    def test_ignora_ausencia_nas_duas_convencoes(self):
        self.assertNotIn("ATT_OMITIDO", self.codigos)
        self.assertNotIn("ATT_VAZIO", self.codigos)

    def test_ordena_por_data_ncm_atributo(self):
        chaves = [(v["vira_obrigatorio_em"], v["ncm"], v["atributo"]) for v in self.vs]
        self.assertEqual(chaves, sorted(chaves))

    def test_vinculo_sem_detalhe_nao_quebra(self):
        vs = coletor.viradas(
            {
                "listaNcm": [
                    {
                        "codigoNcm": "1",
                        "listaAtributos": [
                            {
                                "codigo": "X",
                                "obrigatorio": False,
                                "dataFimVigencia": "2099-01-01",
                            }
                        ],
                    }
                ]
            },
            HOJE,
        )
        self.assertEqual([v["atributo"] for v in vs], ["X"])
        self.assertIsNone(coletor.atributos_das_viradas({}, vs)["X"]["nome"])

    def test_virada_carrega_so_o_que_e_do_vinculo(self):
        # Nome, órgãos e forma são do atributo e moram no mapa "atributos":
        # repetidos em cada virada, uma virada em massa multiplicava o mesmo
        # nome por 10 mil linhas no snapshot.
        self.assertEqual(
            set(self.vs[0]),
            {"ncm", "atributo", "vira_obrigatorio_em", "vigente_desde", "modalidade"},
        )

    def test_atributos_das_viradas_uma_entrada_por_atributo(self):
        mapa = coletor.atributos_das_viradas(self.dados, self.vs)
        self.assertEqual(set(mapa), set(self.codigos))
        self.assertEqual(mapa["ATT_FUTURO"]["nome"], "Referência de licenciamento")
        self.assertEqual(mapa["ATT_FUTURO"]["orgaos"], ["INMETRO"])
        self.assertIn("forma_preenchimento", mapa["ATT_FUTURO"])

    def test_listaNcm_ausente(self):
        self.assertEqual(coletor.viradas({}, HOJE), [])


class TestRegistrosCapengas(unittest.TestCase):
    def test_ncm_duplicada_conta_uma_vez(self):
        ncms = coletor.lista_ncms(amostra())
        codigos = [n["codigoNcm"] for n in ncms]
        self.assertEqual(len(codigos), len(set(codigos)))

    def test_ncm_sem_codigo_e_descartada(self):
        # A entrada com ATT_FANTASMA não tem codigoNcm. Antes ela virava
        # None e derrubava o sort com TypeError.
        self.assertNotIn(None, [n["codigoNcm"] for n in coletor.lista_ncms(amostra())])

    def test_vinculo_sem_codigo_e_contado_como_descarte(self):
        c = coletor.contagens(amostra(), HOJE)
        self.assertEqual(c["descartados"], 2)

    def test_detalhe_sem_codigo_e_descartado(self):
        self.assertNotIn("", coletor.dicionario_atributos(amostra()))


class TestSlug(unittest.TestCase):
    def test_dobra_acento(self):
        # Antes o regex comia o "o" e o "a" inteiros: sem-rg-o-declarado.
        self.assertEqual(coletor.slug("Sem órgão declarado"), "sem-orgao-declarado")

    def test_pontuacao(self):
        self.assertEqual(coletor.slug("MIN.DEFESA"), "min-defesa")

    def test_so_simbolos(self):
        self.assertEqual(coletor.slug("***"), "sem-nome")

    def test_colisao_recebe_sufixo(self):
        grupos = coletor.orgaos(
            [
                {"codigo": "A", "orgaos": ["MIN.DEFESA"], "total_ncms": 1},
                {"codigo": "B", "orgaos": ["MIN DEFESA"], "total_ncms": 1},
            ]
        )
        slugs = [g["slug"] for g in grupos]
        self.assertEqual(len(slugs), len(set(slugs)))


class TestDetalhePublico(unittest.TestCase):
    def test_trunca_dominio_e_reporta_o_total(self):
        grande = {
            "codigo": "X",
            "nome": "X",
            "dominio": [{"codigo": str(i), "descricao": f"opcao {i}"} for i in range(200)],
        }
        d = coletor.detalhe_publico(grande)
        self.assertEqual(len(d["dominio"]), coletor.MAX_DOMINIO)
        self.assertEqual(d["dominio_total"], 200)

    def test_preserva_zero_a_esquerda(self):
        dic = coletor.dicionario_atributos(amostra())
        d = coletor.detalhe_publico(dic["ATT_LARGO"])
        self.assertEqual(d["dominio"][0]["codigo"], "01")

    def test_detalhe_vazio(self):
        self.assertEqual(coletor.detalhe_publico({}), {})


class TestFiltroDePagina(unittest.TestCase):
    """O filtro que antes deixava passar 586 páginas chamadas "Destaque"."""

    def setUp(self):
        self.dados = amostra()
        self.vs = coletor.viradas(self.dados, HOJE)
        self.pub = coletor.atributos_publicaveis(self.dados, self.vs)
        self.codigos = {a["codigo"] for a in self.pub}

    def test_corta_clone_de_uma_unica_ncm(self):
        # ATT_CLONE_A e ATT_CLONE_B têm nome, orientação e definição iguais
        # (a menos do número da NCM) e valem para uma NCM cada.
        self.assertNotIn("ATT_CLONE_A", self.codigos)
        self.assertNotIn("ATT_CLONE_B", self.codigos)

    def test_mantem_quem_tem_prosa_propria(self):
        self.assertIn("ATT_OMITIDO", self.codigos)

    def test_mantem_quem_tem_alcance(self):
        self.assertIn("ATT_LARGO", self.codigos)

    def test_virada_entra_sempre(self):
        for v in self.vs:
            self.assertIn(v["atributo"], self.codigos)

    def test_atributo_de_ncm_afetada_entra_sempre(self):
        # Senão a página daquela NCM linkaria para o vazio.
        afetadas = {v["ncm"] for v in self.vs}
        for ncm in coletor.lista_ncms(self.dados):
            if ncm["codigoNcm"] in afetadas:
                for v in coletor.vinculos_de(ncm):
                    self.assertIn(v["codigo"], self.codigos)

    def test_orfao_de_vinculo_nao_vira_pagina(self):
        self.assertNotIn("ATT_ORFAO", self.codigos)

    def test_assinatura_ignora_o_numero_da_ncm(self):
        dic = coletor.dicionario_atributos(self.dados)
        self.assertEqual(
            coletor.assinatura_prosa(dic["ATT_CLONE_A"]),
            coletor.assinatura_prosa(dic["ATT_CLONE_B"]),
        )


def _com_virada_no_clone(dados, data="2026-08-22"):
    """ATT_CLONE_A ganha uma virada: o único jeito de ele ter página é ser
    forçado - vale para uma NCM e a prosa é boilerplate. Devolve `dados`."""
    for ncm in dados["listaNcm"]:
        for v in ncm.get("listaAtributos") or []:
            if v.get("codigo") == "ATT_CLONE_A":
                v["dataFimVigencia"] = data
    return dados


class TestPaginasPermanentes(unittest.TestCase):
    """Página de atributo que já existiu continua existindo.

    Sem isto, um atributo forçado por uma virada (ou citado por uma NCM com
    virada) perdia a página no dia seguinte ao corte - depois de semanas no
    sitemap. ATT_17220/17280/17300/2548 estavam nessa situação em 30/08.
    """

    DIA_SEGUINTE = date(2026, 8, 23)

    def _apura(self, dados, referencia, catalogo_anterior=None):
        with mock.patch.object(coletor, "PISO", apoio.PISO_BAIXO):
            return coletor.apurar(
                dados,
                referencia,
                apoio.meta_de(dados, referencia),
                catalogo_anterior=catalogo_anterior,
            )

    def test_dia_1_com_virada_publica_e_registra_o_permanente(self):
        apuracao = self._apura(_com_virada_no_clone(amostra()), HOJE)
        self.assertIn("ATT_CLONE_A", apuracao.com_pagina)
        self.assertIn("ATT_CLONE_A", apuracao.catalogo["paginas_permanentes"])
        # Todo publicável de hoje entra: a lista é a união com o dia anterior.
        self.assertEqual(
            apuracao.catalogo["paginas_permanentes"], sorted(apuracao.com_pagina)
        )

    def test_dia_2_sem_virada_mantem_a_pagina(self):
        dia_1 = self._apura(_com_virada_no_clone(amostra()), HOJE)
        # Sem o catálogo anterior, a regra de qualidade corta o clone de novo.
        sem_memoria = self._apura(_com_virada_no_clone(amostra()), self.DIA_SEGUINTE)
        self.assertNotIn("ATT_CLONE_A", sem_memoria.com_pagina)
        dia_2 = self._apura(
            _com_virada_no_clone(amostra()), self.DIA_SEGUINTE, dia_1.catalogo
        )
        self.assertIn("ATT_CLONE_A", dia_2.com_pagina)
        self.assertIn("ATT_CLONE_A", dia_2.catalogo["paginas_permanentes"])
        self.assertEqual(
            dia_2.catalogo["paginas_permanentes"], dia_1.catalogo["paginas_permanentes"]
        )
        # Permanente não é virada: o índice "com virada agendada" não o lista.
        por_codigo = {a["codigo"]: a for a in dia_2.catalogo["atributos"]}
        self.assertFalse(por_codigo["ATT_CLONE_A"]["nas_viradas"])

    def test_atributo_que_sumiu_do_arquivo_nao_entra(self):
        dia_1 = self._apura(_com_virada_no_clone(amostra()), HOJE)
        dados = amostra()
        dados["detalhesAtributos"] = [
            d for d in dados["detalhesAtributos"] if d.get("codigo") != "ATT_CLONE_A"
        ]
        for ncm in dados["listaNcm"]:
            ncm["listaAtributos"] = [
                v
                for v in ncm.get("listaAtributos") or []
                if v.get("codigo") != "ATT_CLONE_A"
            ]
        dia_2 = self._apura(dados, self.DIA_SEGUINTE, dia_1.catalogo)
        self.assertNotIn("ATT_CLONE_A", dia_2.com_pagina)
        # Fica na memória: se a Receita devolver o atributo, a página volta.
        self.assertIn("ATT_CLONE_A", dia_2.catalogo["paginas_permanentes"])

    def test_permanente_sem_vinculo_ganha_pagina_com_zero_ncms(self):
        # ATT_ORFAO existe em detalhesAtributos sem nenhuma NCM: por si só
        # não merece página (test_orfao_de_vinculo_nao_vira_pagina), mas se
        # já teve uma, ela fica - com o que há para dizer.
        apuracao = self._apura(amostra(), HOJE, {"paginas_permanentes": ["ATT_ORFAO"]})
        por_codigo = {a["codigo"]: a for a in apuracao.catalogo["atributos"]}
        self.assertIn("ATT_ORFAO", por_codigo)
        self.assertEqual(por_codigo["ATT_ORFAO"]["total_ncms"], 0)
        self.assertEqual(por_codigo["ATT_ORFAO"]["ncms"], [])

    def test_catalogo_anterior_sem_a_chave_e_silencioso(self):
        # O primeiro run depois desta mudança lê um atributos.json sem a chave.
        apuracao = self._apura(amostra(), HOJE, {"versao": "999", "atributos": []})
        self.assertEqual(apuracao.avisos, [])
        self.assertEqual(
            apuracao.catalogo["paginas_permanentes"], sorted(apuracao.com_pagina)
        )

    def test_chave_com_forma_errada_vira_aviso(self):
        apuracao = self._apura(amostra(), HOJE, {"paginas_permanentes": "ATT_X"})
        self.assertTrue(any("paginas_permanentes" in a for a in apuracao.avisos))
        apuracao = self._apura(amostra(), HOJE, {"paginas_permanentes": [1, 2]})
        self.assertTrue(any("paginas_permanentes" in a for a in apuracao.avisos))

    def test_atributos_publicaveis_aceita_o_conjunto_direto(self):
        dados = amostra()
        vs = coletor.viradas(dados, self.DIA_SEGUINTE)
        sem = {a["codigo"] for a in coletor.atributos_publicaveis(dados, vs)}
        com = {
            a["codigo"]
            for a in coletor.atributos_publicaveis(
                dados, vs, permanentes=frozenset({"ATT_CLONE_B", "ATT_INEXISTENTE"})
            )
        }
        self.assertEqual(com - sem, {"ATT_CLONE_B"})


class TestCatalogoAnterior(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.caminho = os.path.join(tmp.name, "atributos.json")

    def _le(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            return coletor.catalogo_anterior(self.caminho), err.getvalue()

    def test_ausente_e_none_sem_aviso(self):
        self.assertEqual(self._le(), (None, ""))

    def test_ilegivel_vira_aviso(self):
        with open(self.caminho, "w", encoding="utf-8") as f:
            f.write("{ nao e json")
        catalogo, err = self._le()
        self.assertIsNone(catalogo)
        self.assertIn("::warning::catálogo anterior ilegível", err)

    def test_nao_objeto_vira_aviso(self):
        with open(self.caminho, "w", encoding="utf-8") as f:
            f.write("[1, 2]")
        catalogo, err = self._le()
        self.assertIsNone(catalogo)
        self.assertIn("::warning::catálogo anterior não é um objeto", err)

    def test_legivel_volta_inteiro(self):
        with open(self.caminho, "w", encoding="utf-8") as f:
            json.dump({"paginas_permanentes": ["ATT_X"]}, f)
        self.assertEqual(self._le(), ({"paginas_permanentes": ["ATT_X"]}, ""))


class TestMapaCompleto(unittest.TestCase):
    def setUp(self):
        self.dados = amostra()
        vs = coletor.viradas(self.dados, HOJE)
        pub = coletor.atributos_publicaveis(self.dados, vs)
        self.com_pagina = {a["codigo"] for a in pub}
        self.mapa = coletor.mapa_completo(self.dados, self.com_pagina)

    def test_pula_ncm_sem_atributo(self):
        self.assertNotIn("9999.99.99", self.mapa["ncms"])

    def test_traz_todas_as_ncms_com_atributo(self):
        self.assertIn("0101.21.00", self.mapa["ncms"])
        self.assertIn("8703.10.00", self.mapa["ncms"])

    def test_quem_nao_tem_pagina_leva_o_dominio_junto(self):
        # O conteúdo não some com o corte: passa a ser exibido na página da NCM.
        clone = self.mapa["atributos"]["ATT_CLONE_A"]
        self.assertIn("d", clone)
        self.assertIn("t", clone)

    def test_quem_tem_pagina_nao_duplica_o_dominio(self):
        largo = self.mapa["atributos"]["ATT_LARGO"]
        self.assertNotIn("d", largo)

    def test_vinculo_carrega_o_fim_de_vigencia_normalizado(self):
        # É deste mapa, cruzado com as viradas, que o gerador monta a ficha
        # da NCM - e só com o fim por vínculo a página pode dizer "prazo
        # vencido" quando a data passou e o vínculo continua opcional.
        self.assertEqual(self.mapa["schema"], coletor.SCHEMA)
        por_codigo = {v[0]: v for v in self.mapa["ncms"]["8415.10.90"]}
        self.assertEqual(
            por_codigo["ATT_FUTURO"], ["ATT_FUTURO", False, "IMPORTACAO", "2099-12-31"]
        )
        self.assertIsNone(por_codigo["ATT_LARGO"][3])
        # "" em detalhesAtributos e chave omitida em listaAtributos: ambos None.
        por_codigo = {v[0]: v for v in self.mapa["ncms"]["0101.21.00"]}
        self.assertIsNone(por_codigo["ATT_VAZIO"][3])
        self.assertIsNone(por_codigo["ATT_OMITIDO"][3])


class TestContagens(unittest.TestCase):
    def test_versao_continua_string(self):
        self.assertIsInstance(coletor.contagens(amostra(), HOJE)["versao"], str)

    def test_entrada_vazia(self):
        c = coletor.contagens({}, HOJE)
        self.assertEqual(c["ncms"], 0)
        self.assertEqual(c["vinculos"], 0)
        self.assertEqual(c["datas_invalidas"], 0)
        self.assertEqual(c["obrigatorio_nao_booleano"], 0)

    def test_conta_ncm_sem_atributo(self):
        self.assertEqual(coletor.contagens(amostra(), HOJE)["ncms_sem_atributo"], 1)

    def test_invariantes_reprovam_descarte(self):
        c = coletor.contagens(amostra(), HOJE)
        nomes = {n: ok for n, ok in coletor.invariantes(c)}
        self.assertFalse(nomes["nenhum registro descartado"])

    def test_conta_obrigatorio_nao_booleano(self):
        dados = amostra()
        dados["listaNcm"][2]["listaAtributos"][0]["obrigatorio"] = "false"
        c = coletor.contagens(dados, HOJE)
        self.assertEqual(c["obrigatorio_nao_booleano"], 1)
        nomes = {n: ok for n, ok in coletor.invariantes(c)}
        self.assertFalse(nomes["obrigatorio sempre booleano"])

    def test_conta_fim_de_vigencia_passado_ainda_opcional(self):
        # ATT_PASSADO (2020-01-01, obrigatorio=false): a hipótese de que a
        # Receita troca o campo na data falhou para ele. ATT_HOJE não conta
        # (fim == hoje é virada, não vencimento) nem ATT_OBRIGATORIO_FUTURO.
        c = coletor.contagens(amostra(), HOJE)
        self.assertEqual(c["fim_vigencia_passado_opcional"], 1)
        self.assertEqual(
            coletor.vencidos_opcionais(amostra(), HOJE),
            [("8436.21.00", "ATT_PASSADO", "2020-01-01")],
        )
        nomes = {n: ok for n, ok in coletor.invariantes(c)}
        self.assertFalse(nomes["nenhum fim de vigência passado ainda opcional"])
        # Antes da data, nada venceu.
        c = coletor.contagens(amostra(), date(2019, 12, 31))
        self.assertEqual(c["fim_vigencia_passado_opcional"], 0)


def _com_data_invalida(valor="2026-02-30"):
    """A fixture com uma dataFimVigencia impossível em ATT_FUTURO."""
    dados = amostra()
    dados["listaNcm"][2]["listaAtributos"][0]["dataFimVigencia"] = valor
    return dados


class TestDatasInvalidas(unittest.TestCase):
    def test_data_invalida_isolada_nao_derruba_e_e_contada(self):
        dados = _com_data_invalida()
        # Não levanta, e o vínculo sai da lista como se não tivesse data.
        codigos = [v["atributo"] for v in coletor.viradas(dados, HOJE)]
        self.assertNotIn("ATT_FUTURO", codigos)
        self.assertIn("ATT_HOJE", codigos)
        c = coletor.contagens(dados, HOJE)
        self.assertEqual(c["datas_invalidas"], 1)
        self.assertEqual(
            coletor.datas_invalidas(dados), [("8415.10.90", "ATT_FUTURO", "2026-02-30")]
        )
        nomes = {n: ok for n, ok in coletor.invariantes(c)}
        self.assertFalse(nomes["nenhuma dataFimVigencia invalida"])

    def test_fixture_limpa_nao_tem_data_invalida(self):
        self.assertEqual(coletor.datas_invalidas(amostra()), [])


class TestValidarForma(unittest.TestCase):
    def test_aceita_a_fixture(self):
        dados = amostra()
        self.assertIsNone(coletor.validar_forma(dados))
        # E o que passou na forma é o que a regra lê: a fixture tem viradas.
        self.assertTrue(coletor.viradas(dados, HOJE))

    def test_obrigatorio_como_string_e_recusado(self):
        # O caso em que viradas() zerava em silêncio e o site publicava
        # "nada agendado" com exit 0.
        dados = amostra()
        dados["listaNcm"][2]["listaAtributos"][0]["obrigatorio"] = "false"
        with self.assertRaisesRegex(RuntimeError, "schema inesperado.*obrigatorio"):
            coletor.validar_forma(dados)

    def test_obrigatorio_ausente_ou_nulo_passa(self):
        dados = amostra()
        del dados["listaNcm"][2]["listaAtributos"][0]["obrigatorio"]
        dados["listaNcm"][2]["listaAtributos"][1]["obrigatorio"] = None
        self.assertIsNone(coletor.validar_forma(dados))
        self.assertEqual(coletor.contagens(dados, HOJE)["obrigatorio_nao_booleano"], 0)

    def test_listaNcm_nao_lista_e_recusada(self):
        dados = amostra()
        dados["listaNcm"] = {"codigoNcm": "0101.21.00"}
        with self.assertRaisesRegex(RuntimeError, "listaNcm não é lista"):
            coletor.validar_forma(dados)

    def test_detalhes_nao_lista_e_recusado(self):
        dados = amostra()
        dados["detalhesAtributos"] = None
        with self.assertRaisesRegex(RuntimeError, "detalhesAtributos não é lista"):
            coletor.validar_forma(dados)

    def test_versao_numerica_e_recusada(self):
        dados = amostra()
        dados["versao"] = 999
        with self.assertRaisesRegex(RuntimeError, "versao"):
            coletor.validar_forma(dados)

    def test_orgaos_como_string_e_recusado(self):
        dados = amostra()
        dados["detalhesAtributos"][0]["orgaos"] = "INMETRO"
        with self.assertRaisesRegex(RuntimeError, "orgaos"):
            coletor.validar_forma(dados)

    def test_orgaos_e_dominio_nulos_passam(self):
        dados = amostra()
        dados["detalhesAtributos"][0]["orgaos"] = None
        dados["detalhesAtributos"][0]["dominio"] = None
        self.assertIsNone(coletor.validar_forma(dados))
        # None vira lista vazia no que é publicado, nunca TypeError.
        detalhe = coletor.dicionario_atributos(dados)["ATT_OMITIDO"]
        self.assertEqual(coletor.detalhe_publico(detalhe)["orgaos"], [])
        self.assertEqual(coletor.detalhe_publico(detalhe)["dominio"], [])

    def test_raiz_nao_objeto(self):
        with self.assertRaisesRegex(RuntimeError, "schema inesperado"):
            coletor.validar_forma([])

    def test_lista_de_problemas_e_truncada(self):
        dados = amostra()
        for ncm in dados["listaNcm"]:
            for v in ncm["listaAtributos"]:
                v["obrigatorio"] = "sim"
        with self.assertRaisesRegex(RuntimeError, r"e mais \d+") as ctx:
            coletor.validar_forma(dados)
        self.assertLess(len(str(ctx.exception)), 2000)


class TestPortaoDeSanidade(unittest.TestCase):
    BOA = {
        "versao": "346",
        "ncms": 10571,
        "vinculos": 73248,
        "atributos_distintos": 1311,
        "detalhes_atributos": 1311,
        "obrigatorios": 15080,
    }

    def test_aceita_colheita_boa(self):
        self.assertEqual(coletor.conferir_sanidade(self.BOA), [])

    def test_rejeita_lista_vazia(self):
        # O caso que apagava o site inteiro com exit 0.
        with self.assertRaises(RuntimeError):
            coletor.conferir_sanidade(
                {
                    "versao": "1",
                    "ncms": 0,
                    "vinculos": 0,
                    "atributos_distintos": 0,
                    "detalhes_atributos": 0,
                }
            )

    def test_rejeita_versao_ausente_ou_vazia(self):
        for versao in (None, ""):
            with self.subTest(versao=versao):
                with self.assertRaisesRegex(RuntimeError, "versao ausente ou vazia"):
                    coletor.conferir_sanidade(dict(self.BOA, versao=versao))

    def test_rejeita_abaixo_de_cada_piso(self):
        for chave, piso in coletor.PISO.items():
            with self.subTest(chave=chave):
                ruim = dict(self.BOA, **{chave: piso - 1})
                with self.assertRaisesRegex(RuntimeError, chave):
                    coletor.conferir_sanidade(ruim)

    def test_rejeita_obrigatorios_zerados(self):
        # É o número que zera quando `obrigatorio` muda de tipo.
        with self.assertRaisesRegex(RuntimeError, "obrigatorios"):
            coletor.conferir_sanidade(dict(self.BOA, obrigatorios=0))
        with self.assertRaisesRegex(RuntimeError, "obrigatorios caiu"):
            coletor.conferir_sanidade(dict(self.BOA, obrigatorios=10000), self.BOA)

    def test_rejeita_obrigatorio_nao_booleano(self):
        with self.assertRaisesRegex(RuntimeError, "booleano"):
            coletor.conferir_sanidade(dict(self.BOA, obrigatorio_nao_booleano=3))

    def test_rejeita_queda_brusca(self):
        anterior = dict(self.BOA)
        atual = dict(self.BOA, ncms=8000)
        with self.assertRaises(RuntimeError):
            coletor.conferir_sanidade(atual, anterior)

    def test_rejeita_queda_em_cada_chave_rolante(self):
        # Cada uma das contagens comparadas com ontem, sozinha, segura o
        # portão - e a mensagem diz qual.
        for chave in coletor.ROLANTES:
            with self.subTest(chave=chave):
                atual = dict(self.BOA, **{chave: int(self.BOA[chave] * 0.8)})
                with self.assertRaisesRegex(RuntimeError, f"{chave} caiu de"):
                    coletor.conferir_sanidade(atual, self.BOA)

    def test_fronteira_exata_dos_10_por_cento(self):
        # 10% de 10571 é 1057,1: 9514 ainda está dentro, 9513 não.
        self.assertEqual(coletor.conferir_sanidade(dict(self.BOA, ncms=9514), self.BOA), [])
        with self.assertRaisesRegex(RuntimeError, "ncms caiu de 10571 para 9513"):
            coletor.conferir_sanidade(dict(self.BOA, ncms=9513), self.BOA)

    def test_tolera_variacao_pequena(self):
        anterior = dict(self.BOA)
        atual = dict(self.BOA, ncms=10500, vinculos=73000)
        self.assertEqual(coletor.conferir_sanidade(atual, anterior), [])

    def test_crescimento_passa(self):
        maior = {k: (v * 3 if isinstance(v, int) else v) for k, v in self.BOA.items()}
        self.assertEqual(coletor.conferir_sanidade(maior, self.BOA), [])

    def test_anterior_sem_a_chave_e_ignorado(self):
        # O snapshot de ontem pode ser de um código que ainda não contava
        # "obrigatorios": sem a chave não há com que comparar, e a chave
        # nova não pode inventar uma queda.
        anterior = {k: v for k, v in self.BOA.items() if k != "obrigatorios"}
        atual = dict(self.BOA, obrigatorios=6000)
        self.assertEqual(coletor.conferir_sanidade(atual, anterior), [])
        # As outras continuam vigiadas.
        with self.assertRaisesRegex(RuntimeError, "ncms caiu"):
            coletor.conferir_sanidade(dict(atual, ncms=8000), anterior)

    def test_anterior_zero_nao_divide_nem_acusa(self):
        anterior = dict(self.BOA, obrigatorios=0)
        self.assertEqual(coletor.conferir_sanidade(self.BOA, anterior), [])

    def test_sem_anterior_nao_compara(self):
        self.assertEqual(coletor.conferir_sanidade(self.BOA, None), [])
        self.assertEqual(coletor.conferir_sanidade(self.BOA, {}), [])

    def test_mensagem_lista_todos_os_problemas(self):
        # Um problema por vez custaria um dia por problema: a mensagem traz
        # todos de uma vez, separados por ponto e vírgula.
        ruim = dict(
            self.BOA,
            versao="",
            ncms=8000,
            obrigatorios=100,
            obrigatorio_nao_booleano=2,
            datas_invalidas=500,
        )
        with self.assertRaises(RuntimeError) as ctx:
            coletor.conferir_sanidade(ruim, self.BOA)
        mensagem = str(ctx.exception)
        self.assertTrue(mensagem.startswith("colheita degenerada, nada foi gravado: "))
        for pedaco in (
            "versao ausente ou vazia",
            "obrigatorios=100 abaixo do piso 5000",
            "obrigatorio não booleano em 2 vínculos",
            "datas_invalidas=500",
            "ncms caiu de 10571 para 8000",
            "obrigatorios caiu de 15080 para 100",
        ):
            self.assertIn(pedaco, mensagem)
        self.assertEqual(mensagem.count("; "), 5)

    def test_data_invalida_em_massa_e_recusada_pelo_portao(self):
        # 0,1% de 73.248 = 73,2: 73 passa, 74 não.
        coletor.conferir_sanidade(dict(self.BOA, datas_invalidas=73))
        with self.assertRaisesRegex(RuntimeError, "datas_invalidas"):
            coletor.conferir_sanidade(dict(self.BOA, datas_invalidas=74))

    def test_aceitar_queda_vira_aviso_so_na_base_rolante(self):
        atual = dict(self.BOA, ncms=8000)
        avisos = coletor.conferir_sanidade(atual, self.BOA, aceitar_queda=True)
        self.assertEqual(len(avisos), 1)
        self.assertIn("ncms caiu", avisos[0])
        # Piso absoluto e versão continuam fatais mesmo com a válvula.
        with self.assertRaises(RuntimeError):
            coletor.conferir_sanidade(dict(self.BOA, ncms=10), self.BOA, aceitar_queda=True)
        with self.assertRaises(RuntimeError):
            coletor.conferir_sanidade(
                dict(self.BOA, versao=""), self.BOA, aceitar_queda=True
            )

    def test_valvula_le_o_ambiente(self):
        with mock.patch.dict(os.environ, {"SENTINELA_ACEITAR_QUEDA": "1"}):
            self.assertTrue(coletor.aceitar_queda_por_ambiente())
        with mock.patch.dict(os.environ, {"SENTINELA_ACEITAR_QUEDA": "0"}):
            self.assertFalse(coletor.aceitar_queda_por_ambiente())
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(coletor.aceitar_queda_por_ambiente())


class TestSnapshotAnterior(unittest.TestCase):
    def test_ilegivel_vira_aviso_e_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho = os.path.join(tmp, "ultimo.json")
            with open(caminho, "w", encoding="utf-8") as f:
                f.write("{metade")
            with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                self.assertIsNone(coletor.contagens_anteriores(caminho))
            self.assertIn("::warning::", err.getvalue())
            self.assertIn("ilegível", err.getvalue())

    def test_sem_contagens_vira_aviso_e_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            caminho = os.path.join(tmp, "ultimo.json")
            with open(caminho, "w", encoding="utf-8") as f:
                json.dump({"schema": 1}, f)
            with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                self.assertIsNone(coletor.contagens_anteriores(caminho))
            self.assertIn("sem 'contagens'", err.getvalue())

    def test_inexistente_e_silencioso(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                self.assertIsNone(
                    coletor.contagens_anteriores(os.path.join(tmp, "nada.json"))
                )
            self.assertEqual(err.getvalue(), "")


class TestAvisosDeContexto(unittest.TestCase):
    def test_versao_regrediu_so_quando_numericas(self):
        self.assertTrue(coletor.versao_regrediu("345", "346"))
        self.assertFalse(coletor.versao_regrediu("346", "346"))
        self.assertFalse(coletor.versao_regrediu("347", "346"))
        self.assertFalse(coletor.versao_regrediu("v2", "346"))
        self.assertFalse(coletor.versao_regrediu("346", None))

    def test_data_do_arquivo(self):
        self.assertEqual(
            coletor.data_do_arquivo("ATRIBUTOS_POR_NCM_2026_08_22.json"), "2026-08-22"
        )
        self.assertIsNone(coletor.data_do_arquivo("ATRIBUTOS.json"))
        self.assertIsNone(coletor.data_do_arquivo(None))

    def test_fuso_sem_tzdata_usa_utc_menos_3(self):
        with (
            mock.patch.object(
                coletor, "ZoneInfo", side_effect=ZoneInfoNotFoundError("sem tzdata")
            ),
            mock.patch("sys.stderr", new_callable=io.StringIO) as err,
        ):
            fuso = coletor._fuso()
        self.assertEqual(fuso.utcoffset(datetime(2026, 8, 22, 12)), timedelta(hours=-3))
        self.assertIn("tzdata", err.getvalue())

    def test_fuso_com_tzdata_e_sao_paulo(self):
        try:
            ZoneInfo("America/Sao_Paulo")
        except ZoneInfoNotFoundError:
            self.skipTest("máquina sem tzdata: só o fallback é testável aqui")
        self.assertEqual(str(coletor._fuso()), "America/Sao_Paulo")

    def test_agente_carrega_a_versao(self):
        self.assertIn(coletor.__version__, coletor.AGENTE)


def _zip_de(
    conteudo, nome="ATRIBUTOS_POR_NCM_2026_08_22.json", mtime=(2026, 8, 22, 0, 0, 0)
):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo(nome, date_time=mtime)
        z.writestr(info, conteudo)
    return buf.getvalue()


class RespostaFalsa(io.BytesIO):
    def __init__(self, corpo, tipo="application/zip"):
        super().__init__(corpo)
        self.status = 200
        self.headers = {
            "Content-Type": tipo,
            "Content-Disposition": "attachment; filename=x.zip",
        }

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class RespostaQueCaiNoMeio(RespostaFalsa):
    """Conexão que morre durante o corpo: o erro sai do read(), não do urlopen()."""

    def read(self, *a):
        raise http.client.IncompleteRead(b"")


def _erro_http(codigo, cabecalhos=None):
    return urllib.error.HTTPError(coletor.URL, codigo, "erro", cabecalhos or {}, None)


def _urlopen_em_sequencia(respostas):
    """urlopen falso que devolve (ou levanta) cada item da lista, na ordem."""
    fila = list(respostas)

    def falsa(req, timeout=None):
        r = fila.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    return falsa, fila


class TestBaixar(unittest.TestCase):
    def setUp(self):
        self.corpo = _zip_de(json.dumps(amostra()).encode("utf-8"))
        self.sleep = mock.patch("time.sleep")
        self.sleep_mock = self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def test_hash_e_do_json_e_nao_do_zip(self):
        # Os bytes do ZIP mudam a cada requisição porque o mtime interno é o
        # instante da geração. Só o JSON descompactado tem hash estável.
        corpo = json.dumps(amostra()).encode("utf-8")
        a = _zip_de(corpo, mtime=(2026, 8, 22, 1, 0, 0))
        b = _zip_de(corpo, mtime=(2026, 8, 22, 2, 0, 0))
        self.assertNotEqual(a, b)
        with mock.patch(
            "urllib.request.urlopen", side_effect=[RespostaFalsa(a), RespostaFalsa(b)]
        ):
            m1 = coletor.baixar().meta
            m2 = coletor.baixar().meta
        self.assertEqual(m1["sha256_json"], m2["sha256_json"])

    def test_devolve_o_zip_intacto(self):
        with mock.patch("urllib.request.urlopen", return_value=RespostaFalsa(self.corpo)):
            baixado = coletor.baixar()
        self.assertEqual(baixado.bruto, self.corpo)
        self.assertEqual(
            baixado.meta["arquivo_interno"], "ATRIBUTOS_POR_NCM_2026_08_22.json"
        )

    def test_zip_com_mais_de_um_arquivo(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a.json", "{}")
            z.writestr("b.json", "{}")
        with mock.patch(
            "urllib.request.urlopen", return_value=RespostaFalsa(buf.getvalue())
        ):
            with self.assertRaises(RuntimeError):
                coletor.baixar(tentativas=1)

    def test_pagina_de_manutencao_servida_com_200(self):
        with mock.patch(
            "urllib.request.urlopen",
            return_value=RespostaFalsa(b"<html>em manutencao</html>", tipo="text/html"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                coletor.baixar(tentativas=1)
        # A mensagem traz um pedaço do corpo: é o que diz O QUE veio.
        self.assertIn("em manutencao", str(ctx.exception))

    def test_amostra_do_corpo_cabe_numa_linha(self):
        corpo = b"<html>\n" + b"x" * 500 + b"\n</html>"
        with mock.patch(
            "urllib.request.urlopen", return_value=RespostaFalsa(corpo, tipo="text/html")
        ):
            with self.assertRaises(RuntimeError) as ctx:
                coletor.baixar(tentativas=1)
        self.assertNotIn("\n", str(ctx.exception))
        self.assertLess(len(str(ctx.exception)), 350)

    def test_manda_user_agent_proprio(self):
        capturado = {}

        def falsa(req, timeout=None):
            capturado["ua"] = req.get_header("User-agent")
            return RespostaFalsa(self.corpo)

        with mock.patch("urllib.request.urlopen", side_effect=falsa):
            coletor.baixar()
        self.assertIn("SentinelaDoCatalogo", capturado["ua"])

    def test_nao_manda_accept_json(self):
        # O endpoint devolve 406 para Accept: application/json.
        capturado = {}

        def falsa(req, timeout=None):
            capturado["accept"] = req.get_header("Accept")
            return RespostaFalsa(self.corpo)

        with mock.patch("urllib.request.urlopen", side_effect=falsa):
            coletor.baixar()
        self.assertNotIn("json", (capturado["accept"] or "").lower())

    def test_repete_em_falha_transitoria(self):
        falsa, fila = _urlopen_em_sequencia(
            [urllib.error.URLError("timeout"), RespostaFalsa(self.corpo)]
        )
        with mock.patch("urllib.request.urlopen", side_effect=falsa):
            baixado = coletor.baixar()
        self.assertTrue(baixado.conteudo)
        self.assertEqual(fila, [])

    def test_nao_repete_em_406(self):
        chamadas = []

        def falsa(req, timeout=None):
            chamadas.append(1)
            raise _erro_http(406)

        with mock.patch("urllib.request.urlopen", side_effect=falsa):
            with self.assertRaises(urllib.error.HTTPError):
                coletor.baixar()
        self.assertEqual(len(chamadas), 1)
        self.sleep_mock.assert_not_called()

    def test_incomplete_read_e_retentado(self):
        # IncompleteRead não é URLError: escapava do retry e custava o dia.
        falsa, fila = _urlopen_em_sequencia(
            [RespostaQueCaiNoMeio(self.corpo), RespostaFalsa(self.corpo)]
        )
        with mock.patch("urllib.request.urlopen", side_effect=falsa):
            baixado = coletor.baixar()
        self.assertEqual(baixado.bruto, self.corpo)
        self.assertEqual(fila, [])
        self.assertEqual(self.sleep_mock.call_count, 1)

    def test_connection_reset_e_retentado(self):
        falsa, fila = _urlopen_em_sequencia(
            [ConnectionResetError("reset"), RespostaFalsa(self.corpo)]
        )
        with mock.patch("urllib.request.urlopen", side_effect=falsa):
            coletor.baixar()
        self.assertEqual(fila, [])

    def test_503_esgota_e_levanta_o_ultimo(self):
        falsa, fila = _urlopen_em_sequencia([_erro_http(503)] * 5)
        with mock.patch("urllib.request.urlopen", side_effect=falsa):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                coletor.baixar()
        self.assertEqual(ctx.exception.code, 503)
        self.assertEqual(fila, [])
        self.assertEqual(self.sleep_mock.call_count, 4)

    def test_408_e_429_sao_retentados_e_honram_retry_after(self):
        falsa, fila = _urlopen_em_sequencia(
            [
                _erro_http(429, {"Retry-After": "7"}),
                _erro_http(408),
                _erro_http(429, {"Retry-After": "100000"}),
                RespostaFalsa(self.corpo),
            ]
        )
        with mock.patch("urllib.request.urlopen", side_effect=falsa):
            coletor.baixar()
        self.assertEqual(fila, [])
        esperas = [c.args[0] for c in self.sleep_mock.call_args_list]
        # 7 veio do servidor; 30 é o backoff da 2ª tentativa (sem cabeçalho);
        # 100000 bate no teto.
        self.assertEqual(esperas, [7, 30, coletor.TETO_RETRY_AFTER])

    def test_retry_after_em_data_http(self):
        quando = datetime.now(timezone.utc) + timedelta(seconds=60)
        valor = quando.strftime("%a, %d %b %Y %H:%M:%S GMT")
        segundos = coletor._retry_after(_erro_http(429, {"Retry-After": valor}))
        self.assertTrue(55 <= segundos <= 60, segundos)
        self.assertIsNone(coletor._retry_after(_erro_http(429, {"Retry-After": "?"})))
        self.assertIsNone(coletor._retry_after(_erro_http(429)))

    def test_backoff_e_10_30_90_180(self):
        falsa, fila = _urlopen_em_sequencia(
            [urllib.error.URLError("x")] * 4 + [RespostaFalsa(self.corpo)]
        )
        with mock.patch("urllib.request.urlopen", side_effect=falsa):
            coletor.baixar()
        esperas = [c.args[0] for c in self.sleep_mock.call_args_list]
        self.assertEqual(esperas, [10, 30, 90, 180])

    def test_zip_corrompido_e_retentado(self):
        falsa, fila = _urlopen_em_sequencia(
            [RespostaFalsa(b"PK\x03\x04" + b"lixo" * 10), RespostaFalsa(self.corpo)]
        )
        with mock.patch("urllib.request.urlopen", side_effect=falsa):
            coletor.baixar()
        self.assertEqual(fila, [])
        self.assertEqual(self.sleep_mock.call_count, 1)

    def test_pagina_de_manutencao_e_retentada(self):
        # A manutenção é transitória por definição.
        falsa, fila = _urlopen_em_sequencia(
            [
                RespostaFalsa(b"<html>em manutencao</html>", tipo="text/html"),
                RespostaFalsa(self.corpo),
            ]
        )
        with mock.patch("urllib.request.urlopen", side_effect=falsa):
            coletor.baixar()
        self.assertEqual(fila, [])

    def test_aceita_zip_por_bytes_magicos_com_content_type_errado(self):
        with mock.patch(
            "urllib.request.urlopen",
            return_value=RespostaFalsa(self.corpo, tipo="application/octet-stream"),
        ):
            baixado = coletor.baixar(tentativas=1)
        self.assertEqual(baixado.meta["content_type"], "application/octet-stream")
        self.assertTrue(baixado.conteudo)

    def test_recusa_zip_acima_do_teto(self):
        with (
            mock.patch.object(coletor, "MAX_ZIP", 100),
            mock.patch("urllib.request.urlopen", return_value=RespostaFalsa(self.corpo)),
        ):
            with self.assertRaisesRegex(RuntimeError, "teto"):
                coletor.baixar(tentativas=1)

    def test_recusa_json_acima_do_teto_antes_de_descompactar(self):
        with (
            mock.patch.object(coletor, "MAX_JSON", 10),
            mock.patch("urllib.request.urlopen", return_value=RespostaFalsa(self.corpo)),
            mock.patch.object(zipfile.ZipFile, "read") as leitura,
        ):
            with self.assertRaisesRegex(RuntimeError, "teto"):
                coletor.baixar(tentativas=1)
        leitura.assert_not_called()


class TestMain(unittest.TestCase):
    def _main_com(self, erro):
        with (
            mock.patch.object(coletor, "coletar", side_effect=erro),
            mock.patch("sys.stderr", new_callable=io.StringIO) as err,
        ):
            codigo = coletor.main([])
        return codigo, err.getvalue()

    def test_incomplete_read_devolve_1_com_mensagem(self):
        codigo, err = self._main_com(http.client.IncompleteRead(b""))
        self.assertEqual(codigo, 1)
        self.assertIn("IncompleteRead", err)

    def test_os_error_devolve_1_com_mensagem(self):
        codigo, err = self._main_com(ConnectionResetError("reset"))
        self.assertEqual(codigo, 1)
        self.assertIn("reset", err)

    def test_portao_devolve_1(self):
        codigo, err = self._main_com(RuntimeError("colheita degenerada"))
        self.assertEqual(codigo, 1)
        self.assertIn("colheita degenerada", err)


def _arquivos_em(raiz):
    """Caminhos relativos de tudo que existe abaixo de raiz (vazio se não existe)."""
    achados = []
    for pasta, _, nomes in os.walk(raiz):
        for nome in nomes:
            achados.append(
                os.path.relpath(os.path.join(pasta, nome), raiz).replace(os.sep, "/")
            )
    return sorted(achados)


class TestColetarFimAFim(unittest.TestCase):
    """coletar() inteira, num diretório temporário, com a rede falsa."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pilha = contextlib.ExitStack()
        self.addCleanup(pilha.close)
        # O ambiente já baixa o PISO para o tamanho da fixture e fecha a
        # válvula do portão.
        self.caminhos = pilha.enter_context(apoio.ambiente(tmp.name, com_templates=False))
        self.dir = self.caminhos.dados

    def _coletar(self, dados, referencia=HOJE, origem=None):
        corpo = _zip_de(json.dumps(dados).encode("utf-8"))
        with (
            mock.patch(
                "urllib.request.urlopen", side_effect=lambda *a, **k: RespostaFalsa(corpo)
            ),
            mock.patch("sys.stderr", new_callable=io.StringIO) as err,
        ):
            snapshot = coletor.coletar(self.caminhos, referencia=referencia, origem=origem)
        return snapshot, err.getvalue()

    def test_colheita_degenerada_nao_grava_nada(self):
        # O portão vem antes de QUALQUER escrita - inclusive do ZIP bruto.
        with self.assertRaisesRegex(RuntimeError, "colheita degenerada"):
            self._coletar({"versao": "1", "listaNcm": [], "detalhesAtributos": []})
        self.assertEqual(_arquivos_em(self.dir), [])

    def test_forma_errada_nao_grava_nada(self):
        dados = amostra()
        dados["listaNcm"][2]["listaAtributos"][0]["obrigatorio"] = "false"
        with self.assertRaisesRegex(RuntimeError, "schema inesperado"):
            self._coletar(dados)
        self.assertEqual(_arquivos_em(self.dir), [])

    def test_colheita_boa_grava_os_arquivos_sem_tmp(self):
        snapshot, err = self._coletar(amostra())
        self.assertEqual(
            _arquivos_em(self.dir),
            [
                "atributos.json",
                "bruto.zip",
                "completo.json",
                "historico/2026-08-22.json",
                "ultimo.json",
            ],
        )
        self.assertTrue(snapshot["gravado"])
        self.assertTrue(snapshot["catalogo_reescrito"])
        self.assertTrue(snapshot["bruto_novo"])
        self.assertFalse(snapshot["conteudo_identico"])
        self.assertFalse(snapshot["portao_ignorado"])
        self.assertTrue(snapshot["http"]["arquivo_do_dia"])
        self.assertEqual(snapshot["datas_invalidas_amostra"], [])
        with open(os.path.join(self.dir, "bruto.zip"), "rb") as f:
            self.assertEqual(f.read(), _zip_de(json.dumps(amostra()).encode("utf-8")))
        with open(os.path.join(self.dir, "atributos.json"), encoding="utf-8") as f:
            catalogo = json.load(f)
        self.assertNotIn("atualizado_em", catalogo)
        self.assertEqual(catalogo["versao"], "999")
        with open(os.path.join(self.dir, "ultimo.json"), encoding="utf-8") as f:
            gravado = json.load(f)
        self.assertEqual(gravado["contagens"], snapshot["contagens"])
        self.assertNotIn("gravado", gravado)
        self.assertNotIn("::warning::arquivo interno", err)

    def test_segundo_run_identico_devolve_gravado_false_e_conteudo_identico(self):
        self._coletar(amostra())
        mtime_catalogo = os.path.getmtime(os.path.join(self.dir, "atributos.json"))
        segundo, _ = self._coletar(amostra())
        self.assertFalse(segundo["gravado"])
        self.assertTrue(segundo["conteudo_identico"])
        self.assertFalse(segundo["bruto_novo"])
        self.assertFalse(segundo["catalogo_reescrito"])
        # Mesmo JSON e mesmas viradas: o catálogo é recalculado (é função
        # também da regra em merece_pagina), mas nada é reescrito.
        self.assertEqual(
            os.path.getmtime(os.path.join(self.dir, "atributos.json")), mtime_catalogo
        )
        self.assertGreater(segundo["atributos_publicaveis"], 0)
        self.assertEqual(
            _arquivos_em(self.dir),
            [
                "atributos.json",
                "bruto.zip",
                "completo.json",
                "historico/2026-08-22.json",
                "ultimo.json",
            ],
        )

    def test_mesmo_json_em_outro_dia_grava_snapshot_novo(self):
        # Mesmo JSON, outro dia: ATT_HOJE (2026-08-22) já passou. O catálogo
        # depende das viradas, então muda mesmo com o JSON idêntico.
        self._coletar(amostra())
        segundo, _ = self._coletar(amostra(), referencia=date(2026, 8, 23))
        self.assertTrue(segundo["conteudo_identico"])
        self.assertFalse(segundo["bruto_novo"])
        self.assertTrue(segundo["gravado"])
        self.assertIn("historico/2026-08-23.json", _arquivos_em(self.dir))

    def test_catalogo_desatualizado_em_disco_e_reescrito_com_json_identico(self):
        # A regra do catálogo (merece_pagina) mora no código. Se o arquivo em
        # disco veio de uma regra antiga, o run seguinte tem de corrigi-lo
        # mesmo que a Receita não tenha mudado nada - era assim que 888
        # páginas de atributo sobreviviam à regra que as cortou para 391.
        self._coletar(amostra())
        caminho = os.path.join(self.dir, "atributos.json")
        with open(caminho, encoding="utf-8") as f:
            catalogo = json.load(f)
        catalogo["atributos"].append({"codigo": "ATT_REGRA_ANTIGA"})
        catalogo["atualizado_em"] = "2026-08-21"
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(catalogo, f)
        segundo, _ = self._coletar(amostra())
        self.assertTrue(segundo["conteudo_identico"])
        self.assertTrue(segundo["catalogo_reescrito"])
        with open(caminho, encoding="utf-8") as f:
            corrigido = json.load(f)
        self.assertNotIn("atualizado_em", corrigido)
        self.assertNotIn("ATT_REGRA_ANTIGA", {a["codigo"] for a in corrigido["atributos"]})

    def test_pagina_de_atributo_sobrevive_ao_corte_entre_dois_runs(self):
        # Dia 22: ATT_CLONE_A vira hoje e ganha página. Dia 23: a virada
        # passou; sem memória o clone era cortado e a URL virava 404. O
        # coletor lê o atributos.json de ontem e mantém a página.
        primeiro, _ = self._coletar(_com_virada_no_clone(amostra()))
        self.assertIn("ATT_CLONE_A", {v["atributo"] for v in primeiro["viradas"]})
        segundo, _ = self._coletar(
            _com_virada_no_clone(amostra()), referencia=date(2026, 8, 23)
        )
        self.assertNotIn("ATT_CLONE_A", {v["atributo"] for v in segundo["viradas"]})
        with open(os.path.join(self.dir, "atributos.json"), encoding="utf-8") as f:
            catalogo = json.load(f)
        self.assertIn("ATT_CLONE_A", {a["codigo"] for a in catalogo["atributos"]})
        self.assertIn("ATT_CLONE_A", catalogo["paginas_permanentes"])
        # A lista não encolhe: é o mesmo conjunto de ontem.
        self.assertEqual(
            segundo["atributos_publicaveis"], primeiro["atributos_publicaveis"]
        )

    def test_catalogo_sumido_do_disco_volta_no_run_seguinte(self):
        self._coletar(amostra())
        os.remove(os.path.join(self.dir, "completo.json"))
        segundo, _ = self._coletar(amostra())
        self.assertTrue(segundo["conteudo_identico"])
        self.assertIn("completo.json", _arquivos_em(self.dir))

    def test_conteudo_novo_marca_bruto_novo(self):
        self._coletar(amostra())
        dados = amostra()
        dados["versao"] = "1000"
        segundo, _ = self._coletar(dados)
        self.assertTrue(segundo["bruto_novo"])
        self.assertFalse(segundo["conteudo_identico"])
        self.assertTrue(segundo["gravado"])

    def _amostra_encolhida(self):
        dados = amostra()
        fora = {"8436.21.00", "8504.21.00", "8544.42.00"}
        dados["listaNcm"] = [n for n in dados["listaNcm"] if n.get("codigoNcm") not in fora]
        return dados

    def test_queda_rolante_e_fatal_por_padrao(self):
        self._coletar(amostra())
        with self.assertRaisesRegex(RuntimeError, "ncms caiu"):
            self._coletar(self._amostra_encolhida())

    def test_aceitar_queda_vira_aviso(self):
        self._coletar(amostra())
        with mock.patch.dict(os.environ, {"SENTINELA_ACEITAR_QUEDA": "1"}):
            segundo, err = self._coletar(self._amostra_encolhida())
        self.assertTrue(segundo["portao_ignorado"])
        self.assertTrue(segundo["gravado"])
        self.assertIn("::warning::queda aceita", err)
        self.assertIn("ncms caiu", err)

    def test_valvula_sem_queda_nao_marca_portao_ignorado(self):
        with mock.patch.dict(os.environ, {"SENTINELA_ACEITAR_QUEDA": "1"}):
            snapshot, _ = self._coletar(amostra())
        self.assertFalse(snapshot["portao_ignorado"])

    def test_valvula_nao_salva_piso_absoluto(self):
        with mock.patch.dict(os.environ, {"SENTINELA_ACEITAR_QUEDA": "1"}):
            with self.assertRaisesRegex(RuntimeError, "abaixo do piso"):
                self._coletar(
                    {"versao": "1", "listaNcm": [], "detalhesAtributos": [{"codigo": "X"}]}
                )
        self.assertEqual(_arquivos_em(self.dir), [])

    def test_data_invalida_isolada_entra_no_snapshot(self):
        # Na fixture, 1 em 14 vínculos é 7%: o limite de produção (0,1%)
        # recusaria. O que se prova aqui é o caminho, não a proporção.
        with mock.patch.object(coletor, "LIMITE_DATAS_INVALIDAS", 0.5):
            snapshot, _ = self._coletar(_com_data_invalida())
        self.assertEqual(snapshot["contagens"]["datas_invalidas"], 1)
        self.assertEqual(
            snapshot["datas_invalidas_amostra"],
            [["8415.10.90", "ATT_FUTURO", "2026-02-30"]],
        )
        self.assertIn("nenhuma dataFimVigencia invalida", snapshot["invariantes_falhas"])

    def test_arquivo_de_outro_dia_vira_aviso(self):
        snapshot, err = self._coletar(amostra(), referencia=date(2026, 8, 23))
        self.assertFalse(snapshot["http"]["arquivo_do_dia"])
        self.assertIn("::warning::arquivo interno", err)

    def test_versao_regredida_vira_aviso(self):
        self._coletar(amostra())
        dados = amostra()
        dados["versao"] = "998"
        _, err = self._coletar(dados)
        self.assertIn("::warning::versao regrediu", err)

    def test_invariantes_falhas_vao_para_o_snapshot(self):
        snapshot, _ = self._coletar(amostra())
        # A fixture tem dois vínculos sem código, de propósito.
        self.assertIn("nenhum registro descartado", snapshot["invariantes_falhas"])

    def test_main_imprime_warnings_do_actions(self):
        with mock.patch.object(coletor, "LIMITE_DATAS_INVALIDAS", 0.5):
            snapshot, _ = self._coletar(_com_data_invalida())
        with (
            mock.patch.object(coletor, "coletar", return_value=snapshot),
            mock.patch("sys.stderr", new_callable=io.StringIO) as err,
            mock.patch("sys.stdout", new_callable=io.StringIO) as out,
        ):
            self.assertEqual(coletor.main([]), 0)
        self.assertIn(
            "::warning::invariante violada: nenhum registro descartado", err.getvalue()
        )
        self.assertIn(
            "::warning::dataFimVigencia inválida na NCM 8415.10.90", err.getvalue()
        )
        self.assertIn("viradas agendadas", out.getvalue())

    def _stderr_do_main(self, snapshot):
        with (
            mock.patch.object(coletor, "coletar", return_value=snapshot),
            mock.patch("sys.stderr", new_callable=io.StringIO) as err,
            mock.patch("sys.stdout", new_callable=io.StringIO),
        ):
            coletor.main([])
        return err.getvalue()

    def test_main_avisa_fim_de_vigencia_futuro_sem_virada(self):
        # Datas no futuro, mas nenhum vínculo diz obrigatorio=false (a chave
        # some dos opcionais; os obrigatórios ficam, para passar no portão):
        # o filtro de viradas() não enxerga nada - é o sintoma de forma que
        # o aviso existe para pegar.
        dados = amostra()
        for ncm in dados["listaNcm"]:
            for v in ncm.get("listaAtributos", []):
                if v.get("obrigatorio") is False:
                    del v["obrigatorio"]
        snapshot, _ = self._coletar(dados)
        self.assertEqual(snapshot["viradas"], [])
        self.assertGreater(snapshot["contagens"]["com_fim_vigencia_futuro"], 0)
        self.assertIn("nenhuma virada agendada", self._stderr_do_main(snapshot))

    def test_apurar_e_pura_e_nao_escreve_nada(self):
        apuracao = coletor.apurar(amostra(), HOJE, apoio.meta_de(amostra(), HOJE))
        self.assertEqual(_arquivos_em(self.dir), [])
        self.assertGreater(len(apuracao.snapshot["viradas"]), 0)
        self.assertEqual(
            apuracao.com_pagina, {a["codigo"] for a in apuracao.catalogo["atributos"]}
        )
        # Quem imprime é quem chama: a fixture tem registros descartados, e o
        # aviso do arquivo fora do dia só existe quando a data não bate.
        self.assertIsNone(apuracao.snapshot["catalogo_reescrito"])
        self.assertNotIn("gravado", apuracao.snapshot)
        outro_dia = coletor.apurar(
            amostra(), date(2026, 8, 23), apoio.meta_de(amostra(), HOJE)
        )
        self.assertTrue(any("não é do dia" in a for a in outro_dia.avisos))

    def test_gravar_preenche_catalogo_reescrito_e_gravado(self):
        apuracao = coletor.apurar(amostra(), HOJE, apoio.meta_de(amostra(), HOJE))
        snapshot = coletor.gravar(apuracao, self.caminhos)
        self.assertTrue(snapshot["catalogo_reescrito"])
        self.assertTrue(snapshot["gravado"])
        self.assertNotIn("bruto.zip", _arquivos_em(self.dir))
        with open(self.caminhos.ultimo, encoding="utf-8") as f:
            chaves = list(json.load(f))
        # A ordem das chaves no arquivo é a de sempre: o diff diário do git
        # depende dela.
        self.assertEqual(
            chaves[-3:], ["catalogo_reescrito", "atributos_publicaveis", "orgaos"]
        )

    def _zip_em_disco(self, dados, nome="bruto.zip"):
        caminho = os.path.join(self.caminhos.raiz, nome)
        with open(caminho, "wb") as f:
            f.write(_zip_de(json.dumps(dados).encode("utf-8")))
        return caminho

    def test_de_arquivo_apura_sem_tocar_a_rede(self):
        # proibir_rede() já faz urlopen levantar; aqui nem o patch local de
        # _coletar entra.
        caminho = self._zip_em_disco(amostra())
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            snapshot = coletor.coletar(self.caminhos, referencia=HOJE, origem=caminho)
        self.assertIsNone(snapshot["http"]["status"])
        self.assertEqual(snapshot["http"]["origem"], "bruto.zip")
        self.assertEqual(
            snapshot["http"]["arquivo_interno"], "ATRIBUTOS_POR_NCM_2026_08_22.json"
        )
        self.assertTrue(snapshot["http"]["arquivo_do_dia"])
        self.assertEqual(
            _arquivos_em(self.dir),
            [
                "atributos.json",
                "bruto.zip",
                "completo.json",
                "historico/2026-08-22.json",
                "ultimo.json",
            ],
        )
        # O snapshot de um ZIP do disco conta como base rolante do seguinte.
        segundo, _ = self._coletar(amostra())
        self.assertTrue(segundo["conteudo_identico"])

    def test_main_aceita_os_argumentos_da_linha_de_comando(self):
        caminho = self._zip_em_disco(amostra())
        with (
            mock.patch("sys.stdout", new_callable=io.StringIO) as out,
            mock.patch("sys.stderr", new_callable=io.StringIO),
        ):
            codigo = coletor.main(
                ["--de-arquivo", caminho, "--dados", self.dir, "--referencia", "2026-08-23"]
            )
        self.assertEqual(codigo, 0)
        self.assertIn("historico/2026-08-23.json", _arquivos_em(self.dir))
        self.assertIn("viradas agendadas", out.getvalue())

    def test_main_recusa_referencia_fora_do_formato(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            with self.assertRaises(SystemExit):
                coletor.main(["--referencia", "23/08/2026"])
        self.assertIn("AAAA-MM-DD", err.getvalue())

    def test_main_avisa_zip_inexistente(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            codigo = coletor.main(["--de-arquivo", os.path.join(self.dir, "nao.zip")])
        self.assertEqual(codigo, 1)
        self.assertIn("não existe", err.getvalue())

    def test_aceitar_queda_por_argumento(self):
        self._coletar(amostra())
        caminho = self._zip_em_disco(self._amostra_encolhida(), "menor.zip")
        with (
            mock.patch("sys.stdout", new_callable=io.StringIO),
            mock.patch("sys.stderr", new_callable=io.StringIO) as err,
        ):
            codigo = coletor.main(
                ["--de-arquivo", caminho, "--dados", self.dir, "--aceitar-queda"]
            )
        self.assertEqual(codigo, 0)
        self.assertIn("::warning::queda aceita", err.getvalue())
        with open(self.caminhos.ultimo, encoding="utf-8") as f:
            self.assertTrue(json.load(f)["portao_ignorado"])

    def test_main_nao_avisa_quando_as_datas_ja_passaram(self):
        # Depois de um corte a lista esvazia legitimamente: todo dia sem lote
        # novo é um dia normal, não um alerta.
        snapshot, _ = self._coletar(amostra(), referencia=date(2100, 1, 1))
        self.assertEqual(snapshot["viradas"], [])
        self.assertEqual(snapshot["contagens"]["com_fim_vigencia_futuro"], 0)
        self.assertNotIn("nenhuma virada agendada", self._stderr_do_main(snapshot))

    def test_main_lista_os_pares_com_prazo_vencido(self):
        snapshot, _ = self._coletar(amostra())
        self.assertEqual(
            snapshot["vencidos_opcionais_amostra"],
            [["8436.21.00", "ATT_PASSADO", "2020-01-01"]],
        )
        self.assertIn(
            "nenhum fim de vigência passado ainda opcional", snapshot["invariantes_falhas"]
        )
        self.assertIn(
            "::warning::prazo vencido em 2020-01-01 e ainda opcional: NCM 8436.21.00, "
            "atributo ATT_PASSADO",
            self._stderr_do_main(snapshot),
        )

    def test_main_resume_a_lista_de_viradas_em_massa(self):
        with mock.patch.object(coletor, "MAX_VIRADAS_LISTADAS", 3):
            snapshot, _ = self._coletar(apoio.com_lote(amostra(), 5))
            with (
                mock.patch.object(coletor, "coletar", return_value=snapshot),
                mock.patch("sys.stderr", new_callable=io.StringIO),
                mock.patch("sys.stdout", new_callable=io.StringIO) as out,
            ):
                coletor.main([])
        # 3 da amostra (ATT_HOJE, ATT_FUTURO, ATT_HOSTIL) + 5 do lote.
        self.assertIn("8 viradas agendadas", out.getvalue())
        self.assertIn("... e mais 5 viradas", out.getvalue())
        # O nome e o órgão saem do mapa, não da virada.
        self.assertIn("INMETRO", out.getvalue())


class TestViradaEmMassa(unittest.TestCase):
    """O snapshot tem de crescer com o número de NCMs, não com o quadrado.

    ATT_15540 (cClassTrib) é opcional nas 10.516 NCMs: uma dataFimVigencia
    nele são 10.516 viradas num dia. No schema 1 cada uma carregava nome e
    órgãos, e a ficha inteira de cada NCM ia junto - ~13 MB por dia.
    """

    def setUp(self):
        piso = mock.patch.object(coletor, "PISO", apoio.PISO_BAIXO)
        piso.start()
        self.addCleanup(piso.stop)

    def _snapshot(self, dados):
        return coletor.apurar(dados, HOJE, apoio.meta_de(dados, HOJE)).snapshot

    def test_snapshot_cresce_linearmente(self):
        dados = apoio.com_atributo_em_todas(amostra())
        snapshot = self._snapshot(dados)
        ncms = len(coletor.lista_ncms(dados))
        do_lote = [v for v in snapshot["viradas"] if v["atributo"] == apoio.ATT_LOTE]
        self.assertEqual(len(do_lote), ncms)
        self.assertTrue(all(v["vira_obrigatorio_em"] == apoio.DATA_LOTE for v in do_lote))
        # Uma entrada por atributo, não por virada.
        self.assertEqual(
            set(snapshot["atributos"]),
            {apoio.ATT_LOTE, "ATT_FUTURO", "ATT_HOJE", "ATT_HOSTIL"},
        )
        self.assertEqual(snapshot["atributos"][apoio.ATT_LOTE]["orgaos"], ["RFB"])
        self.assertNotIn("ncms_afetadas", snapshot)
        self.assertEqual(snapshot["schema"], 2)

    def test_custo_por_ncm_e_constante(self):
        def tamanho(n):
            corpo = json.dumps(self._snapshot(apoio.com_lote(amostra(), n)), indent=1)
            return len(corpo.encode("utf-8"))

        base, com_100, com_200 = tamanho(0), tamanho(100), tamanho(200)
        por_ncm = (com_200 - com_100) / 100
        # Só a virada (ncm, atributo, datas, modalidade): nada de nome,
        # órgãos nem ficha. Se alguém voltar a repetir o nome em cada linha,
        # o custo por NCM dobra e isto acusa.
        self.assertLess(por_ncm, 200)
        self.assertAlmostEqual(com_100 - base, com_200 - com_100, delta=por_ncm * 2)


if __name__ == "__main__":
    unittest.main()
