"""Testes do ping do IndexNow. Sem rede: urlopen é substituído em todo teste.

Nos casos em que o script não deve nem tentar o POST, o substituto levanta
AssertionError — assim um vazamento de rede vira teste vermelho, não um
ping real disparado de dentro da suíte.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apoio  # noqa: E402
import coletor  # noqa: E402
import indexnow  # noqa: E402


def setUpModule():
    apoio.proibir_rede()


CFG = {"base_url": "https://exemplo.test", "base_path": "/repo", "indexnow_key": "abc123"}
URLS = ["https://exemplo.test/repo/", "https://exemplo.test/repo/ncm/8415.10.90/"]
SEM_REDE = mock.patch(
    "urllib.request.urlopen", side_effect=AssertionError("o teste tocou a rede")
)


def _resposta(status=200):
    """urlopen falso que devolve um context manager com .status."""
    r = mock.MagicMock()
    r.__enter__.return_value.status = status
    return r


def _erro_http(codigo, corpo=b""):
    return urllib.error.HTTPError(
        indexnow.ENDPOINT, codigo, "Unprocessable Entity", {}, io.BytesIO(corpo)
    )


class Ambiente(unittest.TestCase):
    """Config e mudancas.txt em diretório temporário; nada do repositório."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.arq_config = os.path.join(self.tmp.name, "config.json")
        self.arq_raiz = os.path.join(self.tmp.name, "mudancas.txt")
        self.arq_site = os.path.join(self.tmp.name, "site", "mudancas.txt")
        os.makedirs(os.path.dirname(self.arq_site))
        for p in (
            mock.patch.object(indexnow, "ARQ_CONFIG", self.arq_config),
            mock.patch.object(indexnow, "CANDIDATOS", (self.arq_raiz, self.arq_site)),
        ):
            p.start()
            self.addCleanup(p.stop)

    def config(self, **cfg):
        with open(self.arq_config, "w", encoding="utf-8") as f:
            json.dump(dict(CFG, **cfg), f)

    def mudancas(self, urls, caminho=None):
        with open(caminho or self.arq_raiz, "w", encoding="utf-8") as f:
            f.write("".join(u + "\n" for u in urls))

    def roda(self):
        """Executa main() capturando stdout e stderr."""
        saida, erro = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(saida), contextlib.redirect_stderr(erro):
            codigo = indexnow.main()
        return codigo, saida.getvalue(), erro.getvalue()


class TestSemRede(Ambiente):
    def test_sem_chave_sai_0_sem_rede(self):
        self.config(indexnow_key="")
        self.mudancas(URLS)
        with SEM_REDE:
            codigo, saida, _ = self.roda()
        self.assertEqual(codigo, 0)
        self.assertIn("indexnow_key", saida)

    def test_sem_urls_sai_0_sem_rede(self):
        self.config()
        self.mudancas([])
        with SEM_REDE:
            codigo, saida, _ = self.roda()
        self.assertEqual(codigo, 0)
        self.assertIn("Nenhuma URL", saida)

    def test_sem_arquivo_de_mudancas_sai_0_sem_rede(self):
        # O download-artifact tem continue-on-error: o arquivo pode nem existir.
        self.config()
        with SEM_REDE:
            codigo, _, _ = self.roda()
        self.assertEqual(codigo, 0)

    def test_base_url_vazio_sai_0_com_aviso(self):
        # Erro de configuração, não de rede: sem base_url não há keyLocation,
        # e o POST só devolveria 422. Não vale a tentativa.
        self.config(base_url="")
        self.mudancas(URLS)
        with SEM_REDE:
            codigo, saida, _ = self.roda()
        self.assertEqual(codigo, 0)
        self.assertIn("base_url", saida)

    def test_url_relativa_sai_0_com_aviso(self):
        self.config()
        self.mudancas(["/repo/", "/repo/ncm/8415.10.90/"])
        with SEM_REDE:
            codigo, saida, _ = self.roda()
        self.assertEqual(codigo, 0)
        self.assertIn("absoluta https", saida)

    def test_host_do_base_url_diferente_das_urls_sai_0_com_aviso(self):
        self.config(base_url="https://outro.test")
        self.mudancas(URLS)
        with SEM_REDE:
            codigo, saida, _ = self.roda()
        self.assertEqual(codigo, 0)
        self.assertIn("fora do host", saida)


class TestPayload(unittest.TestCase):
    def test_payload_host_e_keylocation_com_base_path(self):
        p = indexnow.montar_payload(CFG, "abc123", URLS)
        self.assertEqual(p["host"], "exemplo.test")
        self.assertEqual(p["key"], "abc123")
        # Em Pages de projeto o arquivo de chave fica sob /<repo>/; apontar
        # para a raiz do host dava 422 porque a raiz não é nossa.
        self.assertEqual(p["keyLocation"], "https://exemplo.test/repo/abc123.txt")
        self.assertEqual(p["urlList"], URLS)

    def test_payload_sem_base_path(self):
        cfg = dict(CFG, base_path="")
        urls = ["https://exemplo.test/", "https://exemplo.test/ncm/8415.10.90/"]
        p = indexnow.montar_payload(cfg, "abc123", urls)
        self.assertEqual(p["keyLocation"], "https://exemplo.test/abc123.txt")
        self.assertEqual(p["host"], "exemplo.test")

    def test_payload_tolera_barra_final_no_base_url_e_no_base_path(self):
        cfg = dict(CFG, base_url="https://exemplo.test/", base_path="/repo/")
        p = indexnow.montar_payload(cfg, "abc123", URLS)
        self.assertEqual(p["keyLocation"], "https://exemplo.test/repo/abc123.txt")

    def test_payload_e_serializavel_em_json(self):
        corpo = json.loads(json.dumps(indexnow.montar_payload(CFG, "k", URLS)))
        self.assertEqual(set(corpo), {"host", "key", "keyLocation", "urlList"})


class TestLerUrls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _grava(self, nome, texto):
        caminho = os.path.join(self.tmp.name, nome)
        with open(caminho, "w", encoding="utf-8") as f:
            f.write(texto)
        return caminho

    def test_ignora_linhas_em_branco_e_espacos(self):
        caminho = self._grava("a.txt", "\nhttps://x.test/a/  \n\n https://x.test/b/\n")
        self.assertEqual(
            indexnow.ler_urls((caminho,)), ["https://x.test/a/", "https://x.test/b/"]
        )

    def test_prefere_mudancas_da_raiz_ao_de_site(self):
        # No CI o artefato cai na raiz; um site/ antigo local não pode vencer.
        raiz = self._grava("raiz.txt", "https://x.test/da-raiz/\n")
        site = self._grava("site.txt", "https://x.test/do-site/\n")
        self.assertEqual(indexnow.ler_urls((raiz, site)), ["https://x.test/da-raiz/"])
        inexistente = os.path.join(self.tmp.name, "nao-existe.txt")
        self.assertEqual(
            indexnow.ler_urls((inexistente, site)), ["https://x.test/do-site/"]
        )

    def test_sem_candidato_devolve_lista_vazia(self):
        self.assertEqual(indexnow.ler_urls((os.path.join(self.tmp.name, "x"),)), [])


class TestFiltrarHost(unittest.TestCase):
    def test_mantem_so_o_host_da_primeira(self):
        lista = URLS + ["https://invasor.test/x/", URLS[0]]
        mantidas, descartadas = indexnow.filtrar_host(lista)
        self.assertEqual(mantidas, URLS + [URLS[0]])
        self.assertEqual(descartadas, ["https://invasor.test/x/"])

    def test_tudo_no_mesmo_host_nao_descarta_nada(self):
        self.assertEqual(indexnow.filtrar_host(URLS), (URLS, []))


class TestPost(Ambiente):
    """O POST em si, com urlopen falso que guarda a requisição."""

    def _requisicao(self, urlopen):
        return urlopen.call_args[0][0]

    def test_manda_payload_e_user_agent(self):
        self.config()
        self.mudancas(URLS)
        with mock.patch("urllib.request.urlopen", return_value=_resposta()) as u:
            codigo, saida, _ = self.roda()
        self.assertEqual(codigo, 0)
        self.assertIn("200", saida)
        req = self._requisicao(u)
        self.assertEqual(req.full_url, indexnow.ENDPOINT)
        self.assertEqual(req.get_method(), "POST")
        corpo = json.loads(req.data.decode("utf-8"))
        self.assertEqual(corpo, indexnow.montar_payload(CFG, "abc123", URLS))
        self.assertEqual(req.get_header("Content-type"), "application/json; charset=utf-8")

    def test_manda_user_agent(self):
        # Sem User-Agent vai "Python-urllib/3.x", o primeiro padrão que um
        # WAF corta. Tem de ser o mesmo do coletor, para o log do servidor
        # reconhecer o projeto — e o teste acusa se as duas cópias divergirem.
        self.config()
        self.mudancas(URLS)
        with mock.patch("urllib.request.urlopen", return_value=_resposta()) as u:
            self.roda()
        req = self._requisicao(u)
        self.assertEqual(req.get_header("User-agent"), indexnow.AGENTE)
        self.assertTrue(indexnow.AGENTE.startswith("SentinelaDoCatalogo/"))
        self.assertEqual(indexnow.AGENTE, coletor.AGENTE)

    def test_poda_acima_do_maximo_manda_so_a_primeira(self):
        self.config()
        lista = [
            f"https://exemplo.test/repo/ncm/{i:08d}/" for i in range(indexnow.MAXIMO + 1)
        ]
        self.mudancas(lista)
        with mock.patch("urllib.request.urlopen", return_value=_resposta()) as u:
            codigo, saida, _ = self.roda()
        self.assertEqual(codigo, 0)
        self.assertIn("lote demais", saida)
        corpo = json.loads(self._requisicao(u).data.decode("utf-8"))
        self.assertEqual(corpo["urlList"], lista[:1])

    def test_exatamente_o_maximo_vai_inteiro(self):
        self.config()
        lista = [f"https://exemplo.test/repo/ncm/{i:08d}/" for i in range(indexnow.MAXIMO)]
        self.mudancas(lista)
        with mock.patch("urllib.request.urlopen", return_value=_resposta()) as u:
            self.roda()
        corpo = json.loads(self._requisicao(u).data.decode("utf-8"))
        self.assertEqual(len(corpo["urlList"]), indexnow.MAXIMO)

    def test_422_devolve_1_sem_levantar_e_loga_corpo(self):
        self.config()
        self.mudancas(URLS)
        erro = _erro_http(422, b'{"message":"Invalid key location"}')
        with mock.patch("urllib.request.urlopen", side_effect=erro):
            codigo, _, stderr = self.roda()
        self.assertEqual(codigo, 1)
        self.assertIn("422", stderr)
        self.assertIn("Invalid key location", stderr)

    def test_corpo_do_erro_e_truncado_e_tolera_bytes_invalidos(self):
        self.config()
        self.mudancas(URLS)
        corpo = b"\xff" + b"x" * (indexnow.CORPO_NO_LOG * 3)
        with mock.patch("urllib.request.urlopen", side_effect=_erro_http(500, corpo)):
            codigo, _, stderr = self.roda()
        self.assertEqual(codigo, 1)
        self.assertIn("�", stderr)
        self.assertNotIn("x" * (indexnow.CORPO_NO_LOG + 1), stderr)

    def test_erro_sem_corpo_nao_quebra_o_log(self):
        self.config()
        self.mudancas(URLS)
        erro = urllib.error.HTTPError(indexnow.ENDPOINT, 429, "Too Many", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=erro):
            codigo, _, stderr = self.roda()
        self.assertEqual(codigo, 1)
        self.assertIn("429", stderr)

    def test_rede_fora_devolve_1_sem_levantar(self):
        self.config()
        self.mudancas(URLS)
        erro = urllib.error.URLError("nome não resolvido")
        with mock.patch("urllib.request.urlopen", side_effect=erro):
            codigo, _, stderr = self.roda()
        self.assertEqual(codigo, 1)
        self.assertIn("inacessível", stderr)

    def test_urls_de_outro_host_sao_descartadas_com_aviso(self):
        self.config()
        self.mudancas(URLS + ["https://invasor.test/x/"])
        with mock.patch("urllib.request.urlopen", return_value=_resposta()) as u:
            codigo, _, stderr = self.roda()
        self.assertEqual(codigo, 0)
        self.assertIn("invasor.test", stderr)
        corpo = json.loads(self._requisicao(u).data.decode("utf-8"))
        self.assertEqual(corpo["urlList"], URLS)
        self.assertEqual(corpo["host"], "exemplo.test")

    def test_prefere_mudancas_da_raiz_ao_de_site(self):
        # A mesma regra de ler_urls, vista pelo main(): com os dois arquivos
        # presentes, o POST leva o da raiz (o artefato do CI).
        self.config()
        self.mudancas(["https://exemplo.test/repo/do-site/"], self.arq_site)
        self.mudancas(["https://exemplo.test/repo/da-raiz/"])
        with mock.patch("urllib.request.urlopen", return_value=_resposta()) as u:
            self.roda()
        corpo = json.loads(self._requisicao(u).data.decode("utf-8"))
        self.assertEqual(corpo["urlList"], ["https://exemplo.test/repo/da-raiz/"])


if __name__ == "__main__":
    unittest.main()
