"""Testes de comum.py: caminhos, escrita atômica, config.json e versão."""

import contextlib
import dataclasses
import io
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apoio  # noqa: E402
import comum  # noqa: E402


def setUpModule():
    apoio.proibir_rede()


class TestCaminhos(unittest.TestCase):
    def test_tudo_deriva_da_raiz(self):
        c = comum.Caminhos(raiz="/r")
        self.assertEqual(c.dados, os.path.join("/r", "dados"))
        self.assertEqual(c.historico, os.path.join("/r", "dados", "historico"))
        self.assertEqual(c.ultimo, os.path.join("/r", "dados", "ultimo.json"))
        self.assertEqual(c.atributos, os.path.join("/r", "dados", "atributos.json"))
        self.assertEqual(c.completo, os.path.join("/r", "dados", "completo.json"))
        self.assertEqual(c.lastmod, os.path.join("/r", "dados", "lastmod.json"))
        self.assertEqual(c.bruto, os.path.join("/r", "dados", "bruto.zip"))
        self.assertEqual(c.site, os.path.join("/r", "site"))
        self.assertEqual(c.templates, os.path.join("/r", "templates"))
        self.assertEqual(c.fontes, os.path.join("/r", "fontes"))
        self.assertEqual(c.config, os.path.join("/r", "config.json"))

    def test_dados_e_site_podem_ser_trocados(self):
        # --dados e --saida: o resto continua seguindo a raiz.
        c = comum.Caminhos(raiz="/r", dados="/d", site="/s")
        self.assertEqual(c.ultimo, os.path.join("/d", "ultimo.json"))
        self.assertEqual(c.site, "/s")
        self.assertEqual(c.templates, os.path.join("/r", "templates"))

    def test_padrao_e_a_raiz_do_repositorio(self):
        self.assertTrue(os.path.exists(os.path.join(comum.padrao().raiz, "coletor.py")))

    def test_e_imutavel(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            comum.padrao().raiz = "x"


class TestEscritaAtomica(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.alvo = os.path.join(self.tmp.name, "a.json")

    def _sem_tmp(self):
        self.assertEqual(os.listdir(self.tmp.name), ["a.json"])

    def test_json_legivel_com_quebra_no_fim(self):
        comum.gravar_json_atomico(self.alvo, {"b": 1, "a": "ç"})
        with open(self.alvo, "rb") as f:
            bruto = f.read()
        self.assertEqual(bruto, b'{\n "b": 1,\n "a": "\xc3\xa7"\n}\n')
        self._sem_tmp()

    def test_json_ordenado_e_sem_indentacao_quando_pedido(self):
        # O lastmod.json: uma chave por linha, ordenado, para o diff do git.
        comum.gravar_json_atomico(self.alvo, {"b": 1, "a": 2}, indent=0, sort_keys=True)
        with open(self.alvo, encoding="utf-8") as f:
            self.assertEqual(f.read(), '{\n"a": 2,\n"b": 1\n}\n')

    def test_texto_e_bytes(self):
        comum.gravar_atomico(self.alvo, "x\ny\n")
        with open(self.alvo, "rb") as f:
            self.assertEqual(f.read(), b"x\ny\n")
        comum.gravar_bytes_atomico(self.alvo, b"PK\x03\x04")
        with open(self.alvo, "rb") as f:
            self.assertEqual(f.read(), b"PK\x03\x04")
        self._sem_tmp()

    def test_substitui_o_anterior_inteiro(self):
        comum.gravar_atomico(self.alvo, "x" * 100)
        comum.gravar_atomico(self.alvo, "y")
        with open(self.alvo, encoding="utf-8") as f:
            self.assertEqual(f.read(), "y")


class TestLerJsonTolerante(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.alvo = os.path.join(self.tmp.name, "a.json")

    def test_ausente_e_none_sem_aviso(self):
        avisos = []
        self.assertIsNone(comum.ler_json_tolerante(self.alvo, avisos.append))
        self.assertEqual(avisos, [])

    def test_ilegivel_e_none_com_aviso(self):
        with open(self.alvo, "w", encoding="utf-8") as f:
            f.write("{metade")
        avisos = []
        self.assertIsNone(comum.ler_json_tolerante(self.alvo, avisos.append))
        self.assertEqual(len(avisos), 1)
        # Sem callback continua silencioso, e continua None.
        self.assertIsNone(comum.ler_json_tolerante(self.alvo))

    def test_legivel_devolve_o_objeto(self):
        comum.gravar_json_atomico(self.alvo, [1, 2])
        self.assertEqual(comum.ler_json_tolerante(self.alvo), [1, 2])


class TestEscritaSemChurn(unittest.TestCase):
    def test_nao_reescreve_quando_so_o_relogio_mudou(self):
        with tempfile.TemporaryDirectory() as tmp:
            alvo = os.path.join(tmp, "s.json")
            a = json.dumps({"coletado_em": "2026-08-22T01:00:00", "x": 1}, indent=1)
            b = json.dumps({"coletado_em": "2026-08-22T02:00:00", "x": 1}, indent=1)
            self.assertTrue(comum.reescrever_se_mudou(alvo, a))
            # 16 dos 18 primeiros commits do projeto não carregavam nada além
            # deste campo.
            self.assertFalse(comum.reescrever_se_mudou(alvo, b))

    def test_reescreve_quando_o_conteudo_muda(self):
        with tempfile.TemporaryDirectory() as tmp:
            alvo = os.path.join(tmp, "s.json")
            a = json.dumps({"coletado_em": "2026-08-22T01:00:00", "x": 1}, indent=1)
            b = json.dumps({"coletado_em": "2026-08-22T01:00:00", "x": 2}, indent=1)
            self.assertTrue(comum.reescrever_se_mudou(alvo, a))
            self.assertTrue(comum.reescrever_se_mudou(alvo, b))

    def test_lista_de_volateis_e_parametro(self):
        with tempfile.TemporaryDirectory() as tmp:
            alvo = os.path.join(tmp, "s.json")
            a = json.dumps({"relogio": 1, "x": 1}, indent=1)
            b = json.dumps({"relogio": 2, "x": 1}, indent=1)
            self.assertTrue(comum.reescrever_se_mudou(alvo, a, ignorar=("relogio",)))
            self.assertFalse(comum.reescrever_se_mudou(alvo, b, ignorar=("relogio",)))
            # Com a lista padrão, "relogio" não é volátil: reescreve.
            self.assertTrue(comum.reescrever_se_mudou(alvo, b))


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.alvo = os.path.join(self.tmp.name, "config.json")

    def _carrega(self, conteudo=None):
        if conteudo is not None:
            with open(self.alvo, "w", encoding="utf-8") as f:
                json.dump(conteudo, f)
        erro = io.StringIO()
        with contextlib.redirect_stderr(erro):
            cfg = comum.carregar_config(self.alvo)
        return cfg, erro.getvalue()

    def test_ausente_e_so_os_padroes(self):
        cfg, erro = self._carrega()
        self.assertEqual(cfg, comum.CONFIG_PADRAO)
        self.assertEqual(erro, "")

    def test_aplica_os_padroes_por_cima_do_parcial(self):
        cfg, erro = self._carrega({"base_path": "/repo"})
        self.assertEqual(cfg["base_path"], "/repo")
        self.assertEqual(cfg["base_url"], "")
        self.assertEqual(set(cfg), set(comum.CONFIG_PADRAO))
        self.assertEqual(erro, "")

    def test_chave_desconhecida_vira_aviso_e_nao_erro(self):
        # Um "base_pth" digitado errado passava em silêncio e o site saía
        # sem prefixo, com todo link interno em 404.
        cfg, erro = self._carrega({"base_pth": "/repo"})
        self.assertIn("base_pth", erro)
        self.assertEqual(cfg["base_path"], "")
        self.assertEqual(cfg["base_pth"], "/repo")

    def test_nao_muda_o_padrao_do_modulo(self):
        self._carrega({"base_path": "/repo"})
        self.assertEqual(comum.CONFIG_PADRAO["base_path"], "")


class TestUrls(unittest.TestCase):
    CFG = {"base_url": "https://e.test/", "base_path": "/repo/"}

    def test_prefixo_url_e_absoluta(self):
        self.assertEqual(comum.prefixo(self.CFG), "/repo")
        self.assertEqual(comum.url(self.CFG, "/x/"), "/repo/x/")
        self.assertEqual(comum.absoluta(self.CFG, "/x/"), "https://e.test/repo/x/")
        self.assertEqual(comum.absoluta({}, "/x/"), "/x/")


class TestVersao(unittest.TestCase):
    """__version__ é a versão do projeto; os outros dois lugares a repetem.

    Uma tag `vX.Y.Z` sem entrada no CHANGELOG é uma versão que ninguém
    consegue ler depois, e um pyproject atrasado faz o pacote se apresentar
    com um número que não é o do User-Agent que bate na Receita. Nenhum dos
    dois quebra nada na hora: quebram meses depois, na hora de entender o
    que estava no ar. Daí o teste.
    """

    def _ler(self, nome):
        with open(os.path.join(comum.RAIZ, nome), encoding="utf-8") as f:
            return f.read()

    def test_a_versao_encabeca_o_changelog(self):
        versoes = re.findall(r"^## (\d+\.\d+\.\d+)\b", self._ler("CHANGELOG.md"), re.M)
        self.assertTrue(versoes, "CHANGELOG.md sem nenhuma entrada de versão")
        self.assertEqual(
            versoes[0],
            comum.__version__,
            "a primeira entrada do CHANGELOG.md tem de ser a versão de "
            "comum.__version__ (a mais nova em cima)",
        )

    def test_o_pyproject_declara_a_mesma_versao(self):
        # tomllib é 3.11+; uma linha de regex serve para um arquivo que o
        # runtime nunca lê.
        declarada = re.search(
            r'^version = "(\d+\.\d+\.\d+)"', self._ler("pyproject.toml"), re.M
        )
        self.assertIsNotNone(declarada, "pyproject.toml sem version")
        self.assertEqual(declarada.group(1), comum.__version__)


if __name__ == "__main__":
    unittest.main()
