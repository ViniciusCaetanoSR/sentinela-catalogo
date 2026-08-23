# Contribuir

Uma página. Se algo aqui parecer arbitrário, o `git log` e os comentários do código costumam explicar o porquê — o projeto é pequeno o bastante para ler inteiro.

## Regras de código

- **Biblioteca padrão, só.** `comum.py`, `coletor.py`, `gerar_site.py`, `indexnow.py` e `servir.py` rodam em Python 3.9+ sem `pip install`. Nada de `requests`, `jinja2`, `pydantic`. `pip` entra em um único lugar: o job `lint` do CI (`ruff` e `coverage`), e nunca no job de coleta. Sem `match/case`, sem `X | None` em anotação que rode em runtime, sem `str.removeprefix`, sem `dataclass(slots=True)` — tudo isso é 3.10+.
- **Português do Brasil, com acentos**, em todos os arquivos: código, comentários, docstrings, mensagens, testes, YAML dos workflows, Markdown. Identificadores em português também (`conferir_sanidade`, `gerar_ncms`). A exceção é o que a ferramenta exige em inglês (`name:`, `runs-on:`).
- **Docstrings e comentários explicam o porquê**, não o quê. O código já diz o quê.
- **Funções puras com `referencia` injetável**: tudo que depende de "hoje" recebe a data como parâmetro, para o teste fixar o dia.
- **Escrita atômica**: `comum.gravar_atomico` e irmãs (arquivo `.tmp` + `os.replace`). Nunca `open(..., "w")` direto em `dados/`.
- **Nada de constante de módulo para caminho**: tudo vem de `comum.Caminhos` (no gerador, de `build.caminhos`). É o que permite aos testes rodar inteiros num diretório temporário sem remendar globais.
- **`esc()` em toda interpolação de dado oficial** nos templates. O arquivo da Receita é entrada externa.
- **Constantes nomeadas** para todo número que signifique alguma coisa (`PISO`, `POR_SITEMAP`, `LIMIAR_LOTE`).
- Estilo mecânico é do `ruff` (`pyproject.toml`): linha de 92 colunas, aspas duplas, imports ordenados.

```bash
python -m pip install ruff coverage       # só para desenvolver
python -m ruff check .
python -m ruff format --check .            # ou `ruff format .` para aplicar
```

## Testes

- **Obrigatórios e rápidos.** Os 358 testes rodam em cerca de 7 s e **nunca tocam a rede** — `tests/apoio.py` faz `urlopen` levantar em todo módulo de teste (`proibir_rede()` no `setUpModule`), o coletor é testado com respostas falsas e a fixture em `tests/fixtures/`. Um teste que precise de internet fica vermelho, não lento.
- **Ambiente de teste é `apoio.ambiente(tmp)`**: devolve um `Caminhos` num diretório temporário com templates, fontes e `config.json`; `apoio.montar_dados()` produz `dados/` pelo mesmo caminho da produção (`apurar` + `gravar`). Não remonte o snapshot à mão.
- Todo bug corrigido ganha um teste com nome que diz o que ele prova (`test_colheita_degenerada_nao_grava_nada`).
- `tests/test_integridade.py` gera o site inteiro a partir da fixture e confere todo link interno. Se você mexeu em template, é ele que vai te pegar.
- **Golden files.** `tests/golden/` guarda três páginas geradas da fixture — a home, uma NCM com virada e um atributo com virada. `tests/test_golden.py` gera de novo e compara caractere a caractere, normalizando só o hash de conteúdo dos estáticos (`estilo.<hash>.css`, `app.<hash>.js`). É o que pega a regressão de *marcação*, que a integridade não vê: um `<h1>` que vira `<h2>`, um `aria-label` que some, um `<caption>` perdido num refactor — o site fecha do mesmo jeito e ninguém nota.

Quando a mudança de marcação for deliberada, regrave e **leia o diff**:

```bash
SENTINELA_REGRAVAR_GOLDEN=1 python -m unittest discover -s tests -k golden
```

```powershell
$env:SENTINELA_REGRAVAR_GOLDEN=1; python -m unittest discover -s tests -k golden
Remove-Item Env:SENTINELA_REGRAVAR_GOLDEN   # senão a próxima rodada regrava de novo
```

O teste falha mesmo depois de regravar, de propósito: golden atualizado sem alguém olhar o `git diff` não prova mais nada. Se você mexeu num template e os três arquivos mudaram, isso é o teste funcionando — a pergunta é se cada linha do diff era para acontecer.

```bash
python -m unittest discover -s tests -v
```

## Commits e branches

- **Mensagem = frase imperativa, sem prefixo**: "Tirar RSS do menu", não "feat: tira RSS" nem "ci: ...". O Dependabot segue a mesma regra.
- Uma branch por assunto; PR contra `main`. O CI (`ci.yml`) roda em todo push e PR e tem de ficar verde.

### `dados/` não é parte de `main`

`dados/` é escrito **pelo bot**, todo dia, na branch órfã `dados`. Em `main` a pasta é ignorada pelo git (`/dados/` no `.gitignore`): ela existe no seu disco como worktree daquela branch, e não há como commitá-la aqui nem por engano. Antes disso, o dado morava em `main` e toda branch de feature conflitava na linha que sempre difere.

Monte o worktree uma vez, depois de clonar:

```bash
git worktree add dados dados     # a branch 'dados' vira a pasta dados/
git -C dados pull                 # traz a coleta de hoje
```

Sem ele, `dados/` fica vazio e `python gerar_site.py` não acha o que ler.

Se você rodar `python coletor.py` localmente, o resultado cai no worktree — ou seja, numa cópia da branch do bot. **Não commite**: o bot regenera tudo no run seguinte, e um commit seu ali só cria divergência para o retry dele resolver.

```bash
git -C dados checkout -- .        # desfaz o que a coleta local escreveu
```

Não versionados (e nunca devem ser): `site/`, `dados/completo.json`, `dados/bruto.zip`.

### Mover `dados/` para a branch própria

**Feito em 2026-08-23.** Fica aqui o registro do que foi feito, porque é o que explica o `git worktree` do dia a dia e é o roteiro de como desfazer.

O bot commitava em `main` todo dia, e isso custava duas coisas: `main` não podia exigir pull request (o `GITHUB_TOKEN` não passa por cima de um ruleset, e a coleta pararia no dia seguinte) e toda branch de feature conflitava na linha que sempre difere. A saída foi uma branch órfã `dados`, sem código e sem histórico em comum com `main`, cuja raiz é o que antes vivia em `dados/`.

Os workflows sabem viver dos dois jeitos, e quem decide é a variável de repositório `BRANCH_DADOS`. Vazia, o passo *Onde ficam os dados* define `DIR_DADOS=dados` e `BRANCH_ALVO` = a branch do checkout, e cada passo seguinte faz o que fazia antes da mudança. Em `dados`, um segundo `actions/checkout` traz a branch para `dados-branch/`, e é lá que o coletor grava (`--dados`), o gerador lê (`--dados`) e o commit acontece (`git -C`).

A ordem seguida — e ela importa, se um dia isto for refeito noutro repositório:

1. mergear em `main` o código que lê `BRANCH_DADOS`. Nada muda: a variável ainda não existe;
2. `bash ferramentas/migrar-dados.sh`. Cria a branch `dados` **localmente**, a partir do `dados/` commitado. Não empurra nada, não escreve um byte no diretório de trabalho, recusa rodar com mudança pendente e, na segunda vez, não faz nada;
3. `git push origin dados`;
4. definir `BRANCH_DADOS` = `dados` em *Settings > Secrets and variables > Actions > Variables*. **É aqui que a mudança liga**;
5. rodar *Coletar e publicar* pelo *Run workflow* e conferir, no log, que tudo aponta para `dados-branch/` e que nenhum commit caiu em `main`;
6. só então tirar `dados/` de `main`: `git rm -r --cached dados` e `/dados/` no `.gitignore`. Antes de o passo 5 dar certo, não — o dado do dia ainda está ali;
7. localmente, `git worktree add dados dados`.

Falta um passo, e ele é uma decisão de governança, não de código: **o ruleset em `main`** (PR obrigatório, check `ci`, sem force-push). Ele só passou a ser possível porque o bot não empurra mais aqui.

Para desfazer, apague a variável `BRANCH_DADOS`: no run seguinte o bot volta a gravar em `dados/` dentro de `main` — e aí é preciso destravar a pasta, tirando `/dados/` do `.gitignore` e removendo o worktree (`git worktree remove dados`). A branch `dados` continua onde está, com o histórico do período.

Depois do passo 8, `dados` é o nome de uma branch **e** de uma pasta, e o git não sabe qual você quer: escreva `git log dados --`.

## Preview local

```bash
python gerar_site.py      # usa o dados/ que está no disco; não toca a rede
python servir.py          # http://localhost:8000/<base_path>/
```

`servir.py` tira o `base_path` do caminho e serve `404.html` para o que não existe, como o GitHub Pages faz. Abrir `site/index.html` direto no navegador não funciona: todo link interno carrega o prefixo.

Para ter um `dados/completo.json` local sem bater na Receita, pegue o ZIP do release `bruto-<versao>` mais recente e rode `python coletor.py --de-arquivo <zip>` — ou rode `python coletor.py` uma vez (é um download de ~500 KB, o endpoint é público). Nos dois casos, depois: `git checkout -- dados` (o `completo.json` e o `bruto.zip`, que o git ignora, ficam). Para não tocar no `dados/` do repositório de jeito nenhum, use `--dados <outra pasta>` nos dois scripts (no gerador ele troca só a pasta de dados; templates, fontes e `config.json` continuam vindo da raiz) ou `python gerar_site.py --raiz`/`--saida`.

## Publicar uma versão

A partir da 0.1.0 vale [SemVer](https://semver.org/lang/pt-BR/), e a versão mora num lugar só: `comum.__version__` (o `pyproject.toml` a repete, e um teste exige que os dois batam). Como não há pacote publicado nem API importável, o que a versão versiona são os dois contratos que outra pessoa pode estar consumindo: o `schema` do snapshot em `dados/` e o formato de `site/dados/viradas.{json,csv}`. Quebrar um deles é MAJOR; página, campo ou workflow novo é MINOR; correção que não muda contrato é PATCH.

1. **Suíte verde nos dois interpretadores.** O CI roda 3.9 e 3.12, e é o piso que costuma quebrar (`match/case`, `X | None` em runtime, `str.removeprefix`):

   ```bash
   python -m unittest discover -s tests     # o interpretador do dia
   py -3.9 -m unittest discover -s tests    # e o piso (fora do Windows: python3.9)
   ```

2. **Lint limpo**: `python -m ruff check .` e `python -m ruff format --check .`.
3. **Entrada nova no topo do [CHANGELOG.md](CHANGELOG.md)**, com a data do dia e só os grupos que mudaram. Resuma: quem lê quer saber o que mudou para ele, não a lista de commits.
4. **Bump em `comum.__version__` e em `version` do `pyproject.toml`.** `TestVersao` recusa os dois fora de sincronia e recusa versão que não encabece o changelog — é o que impede tag sem entrada.
5. **Commit e tag anotada**, depois de o CI ficar verde em `main`:

   ```bash
   git tag -a v0.1.0 -m "Sentinela do Catálogo 0.1.0"
   git push origin v0.1.0
   ```

A tag não publica nada: quem publica é o cron de todo dia. Ela existe para que "o que estava no ar em setembro" tenha nome — e para o histórico do projeto não depender só das releases `bruto-<versao>`, que carregam a versão do arquivo da *Receita*, não a deste código.
