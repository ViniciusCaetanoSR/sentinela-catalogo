"""O que os workflows prometem sobre onde o dado vive.

Não existe jeito de rodar um workflow do GitHub aqui, e PyYAML não é
biblioteca padrão - então isto não é um teste do YAML, é um teste de UMA
invariante que o YAML tem de manter e que ninguém enxerga relendo o arquivo:
todo passo que toca o diretório de dados passa por "$DIR_DADOS".

O motivo é o modo de falha da migração para a branch órfã. Um "dados/"
esquecido num passo continua funcionando hoje, com a variável de repositório
BRANCH_DADOS vazia, e continua funcionando no dia da virada - só que
escrevendo no lugar errado, em silêncio: o commit do dia vai para main (que
a essa altura já não guarda dado nenhum) ou o vigia lê um ultimo.json que
ninguém mais atualiza. Nada fica vermelho. Este arquivo fica.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import apoio  # noqa: E402
import comum  # noqa: E402

DIR_WORKFLOWS = os.path.join(comum.RAIZ, ".github", "workflows")

# Os três que leem ou escrevem o diretório de dados. ci.yml fica de fora: ele
# nunca abre um arquivo de lá, só filtra pushes por caminho.
COM_DADOS = ("coletar.yml", "render.yml", "vigia.yml")

# "dados/" como CAMINHO. Entre crases é outra coisa: é o nome do diretório
# sendo citado para uma pessoa ler (o corpo da issue do job "avisar"), e não
# um lugar onde o runner vai escrever.
RE_CAMINHO = re.compile(r"(?<![A-Za-z_])dados/")
RE_CRASES = re.compile(r"`[^`]*`")


def setUpModule():
    apoio.proibir_rede()


def _ler(nome):
    with open(os.path.join(DIR_WORKFLOWS, nome), encoding="utf-8") as f:
        return f.read()


def _linhas_vivas(texto):
    """Só o que o runner executa: comentário (de YAML ou de shell) é prosa."""
    return [linha for linha in texto.split("\n") if not linha.lstrip().startswith("#")]


class TestOndeVaiODado(unittest.TestCase):
    def test_nenhum_caminho_de_dados_fixo(self):
        for nome in COM_DADOS:
            with self.subTest(nome):
                fixos = [
                    linha
                    for linha in _linhas_vivas(_ler(nome))
                    if RE_CAMINHO.search(RE_CRASES.sub("", linha))
                ]
                self.assertEqual(fixos, [], f'{nome} tem "dados/" fora de "$DIR_DADOS"')

    def test_os_dois_scripts_recebem_o_diretorio(self):
        # O coletor e o gerador aceitam --dados; passar sempre é o que faz o
        # YAML ter um caminho só em vez de dois.
        for nome in COM_DADOS:
            for linha in _linhas_vivas(_ler(nome)):
                if re.search(r"python (coletor|gerar_site)\.py", linha):
                    with self.subTest(nome, linha=linha.strip()):
                        self.assertIn("--dados", linha)

    def test_todo_push_de_dado_nomeia_a_branch_alvo(self):
        # `git push` pelado empurra a branch do checkout - certo hoje, errado
        # no dia em que o dado passa a viver noutra branch.
        for nome in COM_DADOS:
            for linha in _linhas_vivas(_ler(nome)):
                if "git" in linha and "push" in linha:
                    with self.subTest(nome, linha=linha.strip()):
                        self.assertIn('push origin "HEAD:$BRANCH_ALVO"', linha)


class TestVariavelDoRepositorio(unittest.TestCase):
    """vars.BRANCH_DADOS aparece em três lugares, e só neles."""

    def test_aparece_exatamente_onde_deve(self):
        for nome in COM_DADOS:
            texto = _ler(nome)
            with self.subTest(nome):
                self.assertEqual(texto.count("vars.BRANCH_DADOS"), 3)
                self.assertEqual(texto.count("BRANCH_DADOS: ${{ vars.BRANCH_DADOS }}"), 1)
                self.assertEqual(texto.count("if: vars.BRANCH_DADOS != ''"), 1)
                self.assertEqual(texto.count("ref: ${{ vars.BRANCH_DADOS }}"), 1)

    def test_o_checkout_da_branch_e_raso_e_fica_de_lado(self):
        for nome in COM_DADOS:
            texto = _ler(nome)
            with self.subTest(nome):
                self.assertIn("path: dados-branch", texto)
                self.assertIn("fetch-depth: 1", texto)

    def test_vazia_e_o_comportamento_de_hoje(self):
        # O ramo `else` do passo que define as variáveis: sem a variável, o
        # diretório é o dados/ do próprio checkout. É o que faz esta branch
        # poder ser mergeada sem mudar nada.
        for nome in COM_DADOS:
            texto = _ler(nome)
            with self.subTest(nome):
                self.assertIn('echo "DIR_DADOS=dados" >> "$GITHUB_ENV"', texto)
                self.assertIn('echo "DIR_DADOS=dados-branch" >> "$GITHUB_ENV"', texto)

    def test_quem_empurra_tambem_decide_a_branch(self):
        # O vigia não empurra nada, então não tem BRANCH_ALVO.
        for nome in ("coletar.yml", "render.yml"):
            texto = _ler(nome)
            with self.subTest(nome):
                self.assertIn('echo "BRANCH_ALVO=$REF" >> "$GITHUB_ENV"', texto)
                self.assertIn('echo "BRANCH_ALVO=$BRANCH_DADOS" >> "$GITHUB_ENV"', texto)
        self.assertNotIn("BRANCH_ALVO", _ler("vigia.yml"))


class TestOrdemDosPassos(unittest.TestCase):
    def test_a_branch_de_dados_chega_antes_do_cache(self):
        """actions/checkout limpa o diretório que recebe.

        No render.yml o completo.json restaurado do cache cai DENTRO de
        dados-branch/; restaurar antes do checkout seria restaurar para o
        lixo, e o job cairia no caminho de reapurar da release toda vez.
        """
        texto = _ler("render.yml")
        self.assertLess(
            texto.index("path: dados-branch"), texto.index("actions/cache/restore")
        )


class TestCI(unittest.TestCase):
    def test_ci_nao_roda_na_branch_de_dados(self):
        """Dois commits por dia numa branch sem código não pedem suíte.

        O nome fica fixo porque um filtro de `on:` não enxerga vars: enquanto
        a branch não existir, a linha não faz nada.
        """
        texto = _ler("ci.yml")
        self.assertIn("branches-ignore:", texto)
        self.assertIn('- "dados"', texto)


if __name__ == "__main__":
    unittest.main()
