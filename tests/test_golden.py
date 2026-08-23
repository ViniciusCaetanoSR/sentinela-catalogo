"""Três páginas de referência, comparadas caractere a caractere.

`test_integridade` prova que o site **fecha**: todo link interno existe,
nenhum placeholder sobrou, todo `<loc>` do sitemap está em disco. Nenhum
teste provava que a **marcação** continua a mesma. Um `<h1>` que vira
`<h2>` num refactor, um `aria-label` que some, o `<caption>` de uma tabela
que se perde: tudo isso passa verde hoje e só aparece no ar.

Os arquivos versionados em `tests/golden/` são o retrato de três páginas
geradas da mesma fixture, com o mesmo `base_path` e a mesma data de
referência - uma de cada família que o gerador monta de jeito diferente: a
home (a página mais composta de todas), uma NCM com virada agendada e um
atributo com virada. O teste gera de novo e compara.

A única coisa normalizada é o hash de conteúdo dos estáticos
(`estilo.<hash>.css`, `app.<hash>.js`): ele muda a cada byte de CSS ou de
JS, que não é marcação nenhuma. A data de referência é fixa, então não
sobra mais nada volátil - nem a contagem de dias, que sai dela.

Quando a diferença for deliberada, regrave - e leia o diff:

    SENTINELA_REGRAVAR_GOLDEN=1 python -m unittest discover -s tests -k golden

O teste falha mesmo depois de regravar, de propósito: golden atualizado sem
ninguém olhar o que mudou é golden que não prova mais nada.
"""

import contextlib
import difflib
import io
import os
import re
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apoio  # noqa: E402
import comum  # noqa: E402
import gerar_site as g  # noqa: E402

DIR_GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")

# A mesma referência de test_integridade, e pelo mesmo motivo: a fixture tem
# uma virada NESTE dia (ATT_HOJE) e outra em 2099, então as duas pontas do
# prazo aparecem no retrato sem depender do relógio de quem roda.
REFERENCIA = date(2026, 8, 22)
BASE_PATH = "/repo"

# Uma página de cada família. As de capítulo, órgão e índice ficam de fora:
# são as mais simples, e todo golden a mais é um arquivo que alguém vai ter
# de reler no dia em que o template mudar.
PAGINAS = (
    "index.html",
    "ncm/8415.10.90/index.html",
    "atributos/ATT_FUTURO/index.html",
)

VAR_REGRAVAR = "SENTINELA_REGRAVAR_GOLDEN"

# estilo.4cc5ab9c.css -> estilo.HASH.css. É o único trecho que muda sem que
# a marcação tenha mudado; se ele entrasse no golden, trocar uma cor do CSS
# quebraria os três arquivos de uma vez, por um motivo que não é este teste.
RE_ESTATICO = re.compile(r"\b(estilo|app)\.[0-9a-f]{8}\.(css|js)\b")
MARCA = "HASH"

# As tabelas do gerador saem numa linha só, de vários KB: sem os dois cortes
# a mensagem de falha vira uma parede que ninguém lê.
MAX_LINHAS_DIFF = 40
MAX_COLUNAS_DIFF = 200

COMO_REGRAVAR = (
    "Se a mudança é deliberada, leia o diff acima e então regrave:\n"
    f"    {VAR_REGRAVAR}=1 python -m unittest discover -s tests -k golden\n"
    f"    (no PowerShell: $env:{VAR_REGRAVAR}=1)\n"
    "O teste falha depois de regravar, de propósito - confira o `git diff` "
    "antes de commitar."
)


def setUpModule():
    apoio.proibir_rede()


def _gerar(destino):
    """O site inteiro num diretório temporário, pelo caminho da produção.

    Diretório novo a cada vez também significa `lastmod.json` ausente: o
    `dateModified` de cada página cai na data de referência, que é fixa.
    """
    with apoio.ambiente(destino, {"base_path": BASE_PATH}) as caminhos:
        apoio.montar_dados(caminhos, apoio.amostra(), REFERENCIA)
        # O gerador fala bastante; aqui só o que ele escreveu importa.
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            codigo = g.main(["--raiz", destino])
    if codigo != 0:
        raise AssertionError(f"gerar_site.main devolveu {codigo}")
    return caminhos.site


def normalizar(html):
    """Tira o hash de conteúdo dos estáticos. Só isso - o resto é marcação."""
    return RE_ESTATICO.sub(rf"\1.{MARCA}.\2", html)


def _ler(caminho):
    with open(caminho, encoding="utf-8") as f:
        return f.read()


def _caminho_golden(pagina):
    return os.path.join(DIR_GOLDEN, *pagina.split("/"))


def _paginas_gravadas():
    """O que existe em tests/golden/, na mesma forma de PAGINAS."""
    gravadas = set()
    for base, _, nomes in os.walk(DIR_GOLDEN):
        for nome in nomes:
            caminho = os.path.join(base, nome)
            gravadas.add(os.path.relpath(caminho, DIR_GOLDEN).replace(os.sep, "/"))
    return gravadas


def _recortar(linha):
    if len(linha) <= MAX_COLUNAS_DIFF:
        return linha
    return linha[:MAX_COLUNAS_DIFF] + " [... resto da linha]"


def _diferenca(pagina, esperado, obtido):
    """O diff da página, cortado em linhas e em colunas, com o modo de uso."""
    linhas = list(
        difflib.unified_diff(
            esperado.split("\n"),
            obtido.split("\n"),
            fromfile=f"golden/{pagina}",
            tofile=f"gerado/{pagina}",
            lineterm="",
            n=1,
        )
    )
    sobra = ""
    if len(linhas) > MAX_LINHAS_DIFF:
        sobra = f"\n[... e mais {len(linhas) - MAX_LINHAS_DIFF} linhas de diff]"
        linhas = linhas[:MAX_LINHAS_DIFF]
    corpo = "\n".join(_recortar(linha) for linha in linhas)
    return f"a marcação de {pagina} mudou.\n{corpo}{sobra}\n\n{COMO_REGRAVAR}"


class TestGolden(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Uma geração só para os dois testes: o build é o caro daqui.
        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.site = _gerar(tmp.name)

    def _gerado(self, pagina):
        caminho = os.path.join(self.site, *pagina.split("/"))
        # Página que sumiu é regressão maior que marcação trocada, e merece
        # dizer isso em vez de estourar um FileNotFoundError.
        if not os.path.exists(caminho):
            self.fail(f"o gerador não publicou {pagina}")
        return normalizar(_ler(caminho))

    def _golden(self, pagina):
        caminho = _caminho_golden(pagina)
        if not os.path.exists(caminho):
            self.fail(f"falta o golden {caminho}.\n\n{COMO_REGRAVAR}")
        return _ler(caminho)

    def test_golden_da_marcacao_das_tres_paginas(self):
        gerados = {pagina: self._gerado(pagina) for pagina in PAGINAS}
        # "1", como a outra válvula do projeto (SENTINELA_ACEITAR_QUEDA).
        if os.environ.get(VAR_REGRAVAR, "").strip() == "1":
            for pagina, html in gerados.items():
                caminho = _caminho_golden(pagina)
                os.makedirs(os.path.dirname(caminho), exist_ok=True)
                comum.gravar_atomico(caminho, html)
            self.fail(
                "golden regravado a pedido de "
                + f"{VAR_REGRAVAR}=1: "
                + ", ".join(PAGINAS)
                + ".\nEste teste falha de propósito depois de regravar - leia o "
                "`git diff` e confira que toda mudança de marcação era para "
                "acontecer. Sem a variável, ele volta a comparar."
            )
        for pagina, html in gerados.items():
            with self.subTest(pagina):
                esperado = self._golden(pagina)
                if html != esperado:
                    self.fail(_diferenca(pagina, esperado, html))

    def test_golden_guarda_o_hash_normalizado_e_nada_mais(self):
        """Sem isto a normalização poderia virar enfeite sem ninguém notar.

        Um golden gravado com o hash real dentro passaria a falhar a cada
        byte de CSS - e um golden órfão, deixado para trás quando PAGINAS
        muda, ficaria no repositório sem nada o conferir.
        """
        for pagina in PAGINAS:
            with self.subTest(pagina):
                golden = self._golden(pagina)
                self.assertNotRegex(golden, RE_ESTATICO)
                self.assertIn(f"estilo.{MARCA}.css", golden)
                self.assertIn(f"app.{MARCA}.js", golden)
        self.assertEqual(_paginas_gravadas(), set(PAGINAS))
