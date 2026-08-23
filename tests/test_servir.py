"""Testes do preview local.

servir.py é o único script sem cobertura nenhuma, e não é decorativo: ele
existe porque o site é gerado para viver sob o base_path e abrir
site/index.html direto no navegador quebra todo link interno. Se o preview
mentir, a conferência antes de publicar mente junto.

Sobe um servidor de verdade em localhost, numa porta que o sistema escolhe.
Não é rede externa: é o loopback, e é a única forma de provar que o
redirecionamento e o 404 do Pages funcionam através do http.server.
"""

import contextlib
import http.client
import io
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apoio  # noqa: E402
import servir  # noqa: E402


def setUpModule():
    apoio.proibir_rede()


class TestBasePath(unittest.TestCase):
    def test_le_o_prefixo_do_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            with apoio.ambiente(tmp, cfg={"base_path": "/repo"}, com_templates=False) as c:
                with mock.patch.object(servir, "CAMINHOS", c):
                    self.assertEqual(servir.base_path(), "/repo")

    def test_sem_config_o_prefixo_e_vazio(self):
        with tempfile.TemporaryDirectory() as tmp:
            with apoio.ambiente(tmp, cfg={"base_path": ""}, com_templates=False) as c:
                with mock.patch.object(servir, "CAMINHOS", c):
                    self.assertEqual(servir.base_path(), "")


class TestServidor(unittest.TestCase):
    """Um site de mentira, servido como o Pages serviria."""

    PREFIXO = "/repo"

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        site = os.path.join(cls._tmp.name, "site")
        os.makedirs(os.path.join(site, "ncm", "8415.10.90"))
        for caminho, corpo in (
            ("index.html", "<h1>raiz</h1>"),
            ("404.html", "<h1>não encontrada</h1>"),
            (os.path.join("ncm", "8415.10.90", "index.html"), "<h1>a ncm</h1>"),
        ):
            with open(os.path.join(site, caminho), "w", encoding="utf-8") as f:
                f.write(corpo)

        # DIR_SITE é lido pelo handler a cada requisição, então trocar o
        # global do módulo basta - não é preciso reimportar servir.py.
        cls._patch = mock.patch.object(servir, "DIR_SITE", site)
        cls._patch.start()
        servir.Handler.prefixo = cls.PREFIXO

        # Porta 0: o sistema escolhe uma livre. Sem log de acesso no stderr.
        servir.Handler.log_message = lambda *a, **k: None
        cls._srv = servir.http.server.ThreadingHTTPServer(("127.0.0.1", 0), servir.Handler)
        cls._thread = threading.Thread(target=cls._srv.serve_forever, daemon=True)
        cls._thread.start()
        cls.porta = cls._srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls._srv.shutdown()
        cls._srv.server_close()
        cls._thread.join(timeout=5)
        cls._patch.stop()
        servir.Handler.prefixo = ""
        cls._tmp.cleanup()

    def _pedir(self, caminho):
        conexao = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=5)
        self.addCleanup(conexao.close)
        conexao.request("GET", caminho)
        resposta = conexao.getresponse()
        return resposta.status, resposta.getheader("Location"), resposta.read()

    def test_raiz_redireciona_para_o_prefixo(self):
        # Sem isto, quem abre localhost:8000 cai num 404 e conclui que o
        # build quebrou.
        status, destino, _ = self._pedir("/")
        self.assertEqual(status, 302)
        self.assertEqual(destino, self.PREFIXO + "/")

    def test_serve_a_home_sob_o_prefixo(self):
        status, _, corpo = self._pedir(self.PREFIXO + "/")
        self.assertEqual(status, 200)
        self.assertIn(b"raiz", corpo)

    def test_serve_uma_pagina_interna(self):
        status, _, corpo = self._pedir(self.PREFIXO + "/ncm/8415.10.90/")
        self.assertEqual(status, 200)
        self.assertIn(b"a ncm", corpo)

    def test_caminho_ausente_devolve_o_404_do_site(self):
        # É o que o Pages faz: a página de erro do projeto, não a do
        # http.server. Sem isto o preview não mostra o 404 real.
        status, _, corpo = self._pedir(self.PREFIXO + "/ncm/0000.00.00/")
        self.assertEqual(status, 404)
        self.assertIn("não encontrada".encode(), corpo)

    def test_caminho_fora_do_prefixo_tambem_cai_no_404(self):
        status, _, corpo = self._pedir("/ncm/8415.10.90/")
        self.assertEqual(status, 404)
        self.assertIn("não encontrada".encode(), corpo)


class TestMain(unittest.TestCase):
    def test_sem_site_avisa_e_sai_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            ausente = os.path.join(tmp, "site")
            saida = io.StringIO()
            with (
                mock.patch.object(servir, "DIR_SITE", ausente),
                mock.patch.object(sys, "argv", ["servir.py"]),
                contextlib.redirect_stdout(saida),
            ):
                self.assertEqual(servir.main(), 1)
            self.assertIn("gerar_site.py", saida.getvalue())


if __name__ == "__main__":
    unittest.main()
