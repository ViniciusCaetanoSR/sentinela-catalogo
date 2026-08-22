"""Testes do coletor. Sem rede: o ZIP e montado em memoria."""

import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.request
import zipfile
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import coletor  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "amostra.json")
# A mesma data que a fixture usa em ATT_HOJE, para exercitar a fronteira.
HOJE = date(2026, 8, 22)


def amostra():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


class TestFimVigencia(unittest.TestCase):
    """As duas convencoes de ausencia, no mesmo arquivo."""

    def test_chave_omitida(self):
        self.assertIsNone(coletor._fim_vigencia({}))

    def test_string_vazia(self):
        # "" < qualquer data e True: comparar isso como data inverte o filtro.
        self.assertIsNone(coletor._fim_vigencia({"dataFimVigencia": ""}))

    def test_data_valida(self):
        self.assertEqual(
            coletor._fim_vigencia({"dataFimVigencia": "2026-08-30"}),
            "2026-08-30")

    def test_data_com_formato_certo_mas_inexistente(self):
        # O regex antigo aceitava 30 de fevereiro; so quebrava no gerador,
        # com o dado ja gravado e commitado.
        with self.assertRaises(ValueError):
            coletor._fim_vigencia({"dataFimVigencia": "2026-02-30"})

    def test_formato_brasileiro(self):
        with self.assertRaises(ValueError):
            coletor._fim_vigencia({"dataFimVigencia": "30/08/2026"})


class TestViradas(unittest.TestCase):
    def setUp(self):
        self.dados = amostra()
        self.vs = coletor.viradas(self.dados, HOJE)
        self.codigos = [v["atributo"] for v in self.vs]

    def test_pega_o_futuro(self):
        self.assertIn("ATT_FUTURO", self.codigos)

    def test_inclui_o_dia_exato_da_virada(self):
        # A fronteira. Com o corte antigo (fim <= ref) o vinculo sumia
        # justamente no dia mais acionavel, e a copia "E hoje." nunca rodava.
        self.assertIn("ATT_HOJE", self.codigos)

    def test_ignora_data_passada(self):
        self.assertNotIn("ATT_PASSADO", self.codigos)

    def test_ignora_quem_ja_e_obrigatorio(self):
        self.assertNotIn("ATT_OBRIGATORIO_FUTURO", self.codigos)

    def test_ignora_ausencia_nas_duas_convencoes(self):
        self.assertNotIn("ATT_OMITIDO", self.codigos)
        self.assertNotIn("ATT_VAZIO", self.codigos)

    def test_ordena_por_data_ncm_atributo(self):
        chaves = [(v["vira_obrigatorio_em"], v["ncm"], v["atributo"])
                  for v in self.vs]
        self.assertEqual(chaves, sorted(chaves))

    def test_vinculo_sem_detalhe_nao_quebra(self):
        coletor.viradas({"listaNcm": [{"codigoNcm": "1", "listaAtributos": [
            {"codigo": "X", "obrigatorio": False,
             "dataFimVigencia": "2099-01-01"}]}]}, HOJE)

    def test_listaNcm_ausente(self):
        self.assertEqual(coletor.viradas({}, HOJE), [])


class TestRegistrosCapengas(unittest.TestCase):
    def test_ncm_duplicada_conta_uma_vez(self):
        ncms = coletor.lista_ncms(amostra())
        codigos = [n["codigoNcm"] for n in ncms]
        self.assertEqual(len(codigos), len(set(codigos)))

    def test_ncm_sem_codigo_e_descartada(self):
        # A entrada com ATT_FANTASMA nao tem codigoNcm. Antes ela virava
        # None e derrubava o sort com TypeError.
        self.assertNotIn(None, [n["codigoNcm"] for n in coletor.lista_ncms(amostra())])

    def test_vinculo_sem_codigo_e_contado_como_descarte(self):
        c = coletor.contagens(amostra(), HOJE)
        self.assertEqual(c["descartados"], 2)

    def test_detalhe_sem_codigo_e_descartado(self):
        self.assertNotIn("", coletor.dicionario_atributos(amostra()))

    def test_ncms_afetadas_ordenadas(self):
        dados = amostra()
        fichas = coletor.ncms_afetadas(dados, coletor.viradas(dados, HOJE))
        self.assertEqual([f["ncm"] for f in fichas],
                         sorted(f["ncm"] for f in fichas))


class TestSlug(unittest.TestCase):
    def test_dobra_acento(self):
        # Antes o regex comia o "o" e o "a" inteiros: sem-rg-o-declarado.
        self.assertEqual(coletor.slug("Sem órgão declarado"),
                         "sem-orgao-declarado")

    def test_pontuacao(self):
        self.assertEqual(coletor.slug("MIN.DEFESA"), "min-defesa")

    def test_so_simbolos(self):
        self.assertEqual(coletor.slug("***"), "sem-nome")

    def test_colisao_recebe_sufixo(self):
        grupos = coletor.orgaos([
            {"codigo": "A", "orgaos": ["MIN.DEFESA"], "total_ncms": 1},
            {"codigo": "B", "orgaos": ["MIN DEFESA"], "total_ncms": 1},
        ])
        slugs = [g["slug"] for g in grupos]
        self.assertEqual(len(slugs), len(set(slugs)))


class TestDetalhePublico(unittest.TestCase):
    def test_trunca_dominio_e_reporta_o_total(self):
        grande = {"codigo": "X", "nome": "X", "dominio": [
            {"codigo": str(i), "descricao": f"opcao {i}"} for i in range(200)]}
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
    """O filtro que antes deixava passar 586 paginas chamadas "Destaque"."""

    def setUp(self):
        self.dados = amostra()
        self.vs = coletor.viradas(self.dados, HOJE)
        self.pub = coletor.atributos_publicaveis(self.dados, self.vs)
        self.codigos = {a["codigo"] for a in self.pub}

    def test_corta_clone_de_uma_unica_ncm(self):
        # ATT_CLONE_A e ATT_CLONE_B tem nome, orientacao e definicao iguais
        # (a menos do numero da NCM) e valem para uma NCM cada.
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
        # Senao a pagina daquela NCM linkaria para o vazio.
        afetadas = {f["ncm"] for f in coletor.ncms_afetadas(self.dados, self.vs)}
        for ncm in coletor.lista_ncms(self.dados):
            if ncm["codigoNcm"] in afetadas:
                for v in coletor.vinculos_de(ncm):
                    self.assertIn(v["codigo"], self.codigos)

    def test_orfao_de_vinculo_nao_vira_pagina(self):
        self.assertNotIn("ATT_ORFAO", self.codigos)

    def test_assinatura_ignora_o_numero_da_ncm(self):
        dic = coletor.dicionario_atributos(self.dados)
        self.assertEqual(coletor.assinatura_prosa(dic["ATT_CLONE_A"]),
                         coletor.assinatura_prosa(dic["ATT_CLONE_B"]))


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
        # O conteudo nao some com o corte: passa a ser exibido na pagina da NCM.
        clone = self.mapa["atributos"]["ATT_CLONE_A"]
        self.assertIn("d", clone)
        self.assertIn("t", clone)

    def test_quem_tem_pagina_nao_duplica_o_dominio(self):
        largo = self.mapa["atributos"]["ATT_LARGO"]
        self.assertNotIn("d", largo)


class TestContagens(unittest.TestCase):
    def test_versao_continua_string(self):
        self.assertIsInstance(coletor.contagens(amostra(), HOJE)["versao"], str)

    def test_entrada_vazia(self):
        c = coletor.contagens({}, HOJE)
        self.assertEqual(c["ncms"], 0)
        self.assertEqual(c["vinculos"], 0)

    def test_conta_ncm_sem_atributo(self):
        self.assertEqual(coletor.contagens(amostra(), HOJE)["ncms_sem_atributo"], 1)

    def test_invariantes_reprovam_descarte(self):
        c = coletor.contagens(amostra(), HOJE)
        nomes = {n: ok for n, ok in coletor.invariantes(c)}
        self.assertFalse(nomes["nenhum registro descartado"])


class TestPortaoDeSanidade(unittest.TestCase):
    BOA = {"versao": "346", "ncms": 10571, "vinculos": 73248,
           "atributos_distintos": 1311, "detalhes_atributos": 1311}

    def test_aceita_colheita_boa(self):
        coletor.conferir_sanidade(self.BOA)

    def test_rejeita_lista_vazia(self):
        # O caso que apagava o site inteiro com exit 0.
        with self.assertRaises(RuntimeError):
            coletor.conferir_sanidade(
                {"versao": "1", "ncms": 0, "vinculos": 0,
                 "atributos_distintos": 0, "detalhes_atributos": 0})

    def test_rejeita_versao_ausente(self):
        ruim = dict(self.BOA, versao=None)
        with self.assertRaises(RuntimeError):
            coletor.conferir_sanidade(ruim)

    def test_rejeita_queda_brusca(self):
        anterior = dict(self.BOA)
        atual = dict(self.BOA, ncms=8000)
        with self.assertRaises(RuntimeError):
            coletor.conferir_sanidade(atual, anterior)

    def test_tolera_variacao_pequena(self):
        anterior = dict(self.BOA)
        atual = dict(self.BOA, ncms=10500, vinculos=73000)
        coletor.conferir_sanidade(atual, anterior)


def _zip_de(conteudo, nome="ATRIBUTOS_POR_NCM_2026_08_22.json", mtime=(2026, 8, 22, 0, 0, 0)):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo(nome, date_time=mtime)
        z.writestr(info, conteudo)
    return buf.getvalue()


class RespostaFalsa(io.BytesIO):
    def __init__(self, corpo, tipo="application/zip"):
        super().__init__(corpo)
        self.status = 200
        self.headers = {"Content-Type": tipo,
                        "Content-Disposition": "attachment; filename=x.zip"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestBaixar(unittest.TestCase):
    def test_hash_e_do_json_e_nao_do_zip(self):
        # Os bytes do ZIP mudam a cada requisicao porque o mtime interno e o
        # instante da geracao. So o JSON descompactado tem hash estavel.
        corpo = json.dumps(amostra()).encode("utf-8")
        a = _zip_de(corpo, mtime=(2026, 8, 22, 1, 0, 0))
        b = _zip_de(corpo, mtime=(2026, 8, 22, 2, 0, 0))
        self.assertNotEqual(a, b)
        with mock.patch("urllib.request.urlopen",
                        side_effect=[RespostaFalsa(a), RespostaFalsa(b)]):
            _, m1 = coletor.baixar()
            _, m2 = coletor.baixar()
        self.assertEqual(m1["sha256_json"], m2["sha256_json"])

    def test_zip_com_mais_de_um_arquivo(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("a.json", "{}")
            z.writestr("b.json", "{}")
        with mock.patch("urllib.request.urlopen",
                        return_value=RespostaFalsa(buf.getvalue())):
            with self.assertRaises(RuntimeError):
                coletor.baixar()

    def test_pagina_de_manutencao_servida_com_200(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=RespostaFalsa(b"<html>em manutencao</html>",
                                                   tipo="text/html")):
            with self.assertRaises(RuntimeError):
                coletor.baixar(tentativas=1)

    def test_manda_user_agent_proprio(self):
        corpo = _zip_de(json.dumps(amostra()).encode("utf-8"))
        capturado = {}

        def falsa(req, timeout=None):
            capturado["ua"] = req.get_header("User-agent")
            return RespostaFalsa(corpo)

        with mock.patch("urllib.request.urlopen", side_effect=falsa):
            coletor.baixar()
        self.assertIn("SentinelaDoCatalogo", capturado["ua"])

    def test_nao_manda_accept_json(self):
        # O endpoint devolve 406 para Accept: application/json.
        corpo = _zip_de(json.dumps(amostra()).encode("utf-8"))
        capturado = {}

        def falsa(req, timeout=None):
            capturado["accept"] = req.get_header("Accept")
            return RespostaFalsa(corpo)

        with mock.patch("urllib.request.urlopen", side_effect=falsa):
            coletor.baixar()
        self.assertNotIn("json", (capturado["accept"] or "").lower())

    def test_repete_em_falha_transitoria(self):
        corpo = _zip_de(json.dumps(amostra()).encode("utf-8"))
        tentativas = [urllib.error.URLError("timeout"), RespostaFalsa(corpo)]

        def falsa(req, timeout=None):
            r = tentativas.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        with mock.patch("urllib.request.urlopen", side_effect=falsa), \
                mock.patch("time.sleep"):
            conteudo, _ = coletor.baixar()
        self.assertTrue(conteudo)
        self.assertEqual(tentativas, [])

    def test_nao_repete_em_406(self):
        chamadas = []

        def falsa(req, timeout=None):
            chamadas.append(1)
            raise urllib.error.HTTPError(coletor.URL, 406, "Not Acceptable",
                                         {}, None)

        with mock.patch("urllib.request.urlopen", side_effect=falsa), \
                mock.patch("time.sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                coletor.baixar()
        self.assertEqual(len(chamadas), 1)


class TestEscritaSemChurn(unittest.TestCase):
    def test_nao_reescreve_quando_so_o_relogio_mudou(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            alvo = os.path.join(tmp, "s.json")
            a = json.dumps({"coletado_em": "2026-08-22T01:00:00", "x": 1}, indent=1)
            b = json.dumps({"coletado_em": "2026-08-22T02:00:00", "x": 1}, indent=1)
            self.assertTrue(coletor._reescrever_se_mudou(alvo, a))
            # 16 dos 18 primeiros commits do projeto nao carregavam nada alem
            # deste campo.
            self.assertFalse(coletor._reescrever_se_mudou(alvo, b))

    def test_reescreve_quando_o_conteudo_muda(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            alvo = os.path.join(tmp, "s.json")
            a = json.dumps({"coletado_em": "2026-08-22T01:00:00", "x": 1}, indent=1)
            b = json.dumps({"coletado_em": "2026-08-22T01:00:00", "x": 2}, indent=1)
            self.assertTrue(coletor._reescrever_se_mudou(alvo, a))
            self.assertTrue(coletor._reescrever_se_mudou(alvo, b))


if __name__ == "__main__":
    unittest.main()
