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


# A indentação do corpo de um `run: |` dentro de um passo, neste projeto.
RECUO_DO_RUN = " " * 10


def _bloco_run(nome, passo):
    """O shell de um passo, extraído pela regra do bloco literal do YAML.

    PyYAML não é biblioteca padrão e este projeto não instala nada em runtime,
    então a extração é textual: o bloco vai até a primeira linha não vazia com
    indentação menor que a do corpo, que é exatamente o que o YAML diz.
    """
    texto = _ler(nome)
    inicio = texto.index(f"- name: {passo}")
    corpo = texto[texto.index("run: |", inicio) :].split("\n")[1:]
    linhas = []
    for linha in corpo:
        if linha.strip() and not linha.startswith(RECUO_DO_RUN):
            break
        linhas.append(linha)
    return "\n".join(linhas).rstrip() + "\n"


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


class TestConferenciaPosDeploy(unittest.TestCase):
    """A conferência espera a borda do Pages, e as duas cópias não divergem.

    O passo lê o status.json pela URL PÚBLICA, e a borda do Pages serve esse
    arquivo com Cache-Control: max-age=600 ignorando tanto cache-buster na
    query quanto Cache-Control: no-cache - os três foram medidos contra o site
    no ar. Entre o deploy responder e a borda entregar o build novo não existe
    atalho: só esperar. Um `curl --retry` NÃO cobre isso, porque ele reage a
    ERRO, e uma borda velha responde 200 com o build anterior.

    Foi o alarme falso de 24, 25 e 26/08: três runs vermelhas em dias que
    gravaram o dado e publicaram o site. Um alerta que grita falso todo dia é
    um alerta que se aprende a ignorar - por isso estas invariantes.
    """

    PASSO = "Conferir o que foi publicado"
    ARQUIVOS = ("coletar.yml", "render.yml")
    # O max-age com que a borda do Pages serve o status.json, medido.
    TTL_DA_BORDA = 600
    # O teto próprio do actions/deploy-pages, que roda no mesmo job.
    TETO_DO_DEPLOY = 600

    def _teto_de_espera(self, nome):
        achado = re.search(r'TETO_ESPERA:\s*"(\d+)"', _ler(nome))
        self.assertIsNotNone(achado, f"{nome}: TETO_ESPERA não está definido")
        return int(achado.group(1))

    def test_as_duas_copias_sao_identicas(self):
        """A duplicação é deliberada; divergir em silêncio não é.

        O job `publicar` não faz checkout (só roda o deploy), então uma
        composite action obrigaria a fazer um - a razão está escrita no
        próprio render.yml. O preço é este teste: consertar uma cópia e
        esquecer a outra deixa metade do defeito de pé, e a metade que fica em
        render.yml é a silenciosa (lá a data esperada é a que já está no ar,
        então uma leitura velha PASSA - verde falso em vez de vermelho falso).
        """
        a, b = (_bloco_run(nome, self.PASSO) for nome in self.ARQUIVOS)
        self.assertEqual(a, b)

    def test_rele_ate_a_borda_entregar_o_build_desta_run(self):
        for nome in self.ARQUIVOS:
            with self.subTest(nome):
                bloco = _bloco_run(nome, self.PASSO)
                # Um curl único, por mais --retry que leve, volta a confundir
                # borda velha com deploy quebrado.
                self.assertIn("while :; do", bloco)
                self.assertIn("sleep", bloco)
                # E o que ele compara é a impressão digital do build, não a
                # data de referência: a data repete entre runs do mesmo dia.
                self.assertIn("$GERADO_EM", bloco)

    def test_o_teto_de_espera_passa_do_ttl_da_borda(self):
        """Esperar menos que o max-age volta a confundir cache com defeito.

        Passado o TTL a borda é obrigada a revalidar na origem: o que não
        bateu até lá deixou de ter cache como explicação.
        """
        for nome in self.ARQUIVOS:
            with self.subTest(nome):
                self.assertGreater(self._teto_de_espera(nome), self.TTL_DA_BORDA)

    def test_o_job_de_publicar_cabe_na_espera(self):
        """Sem folga no job, o runner mata o passo no meio da contagem."""
        for nome in self.ARQUIVOS:
            with self.subTest(nome):
                texto = _ler(nome)
                depois = texto[texto.index("\n  publicar:\n") :]
                minutos = int(re.search(r"timeout-minutes:\s*(\d+)", depois).group(1))
                preciso = self._teto_de_espera(nome) + self.TETO_DO_DEPLOY
                self.assertGreaterEqual(minutos * 60, preciso)


if __name__ == "__main__":
    unittest.main()
