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

### `dados/` nunca entra em branch de feature

`dados/` é escrito **pelo bot**, todo dia, em `main`. Se uma branch de feature carrega mudança em `dados/` — porque você rodou `python coletor.py` localmente, por exemplo — o merge conflita exatamente na linha que sempre difere, e o conflito não tem resolução certa: o bot vai regenerar tudo no próximo run de qualquer jeito.

Antes de commitar, tire `dados/` do que vai entrar:

```bash
git restore --staged --worktree dados
```

Ao rebasear uma branch antiga sobre `main`, pegue o `dados/` de lá em vez de resolver conflito:

```bash
git checkout origin/main -- dados
```

A única exceção é `dados/lastmod.json` quando a mudança **é** no gerador e altera o que ele grava ali — e mesmo assim, prefira deixar o bot regravar.

Não versionados (e nunca devem ser): `site/`, `dados/completo.json`, `dados/bruto.zip`.

### Mover `dados/` para a branch própria

Está **pronto e desligado**. O bot commita em `main` todo dia, e isso custa duas coisas: `main` não pode exigir pull request (o `GITHUB_TOKEN` não passa por cima de um ruleset, e a coleta pararia no dia seguinte) e toda branch de feature conflita na linha que sempre difere. A saída é uma branch órfã `dados`, sem código e sem histórico em comum com `main`, cuja raiz é o que hoje vive em `dados/`.

Os workflows já sabem viver dos dois jeitos, e quem decide é a variável de repositório `BRANCH_DADOS`. **Enquanto ela não existir, nada muda**: o passo *Onde ficam os dados* define `DIR_DADOS=dados` e `BRANCH_ALVO` = a branch do checkout, e cada passo seguinte faz exatamente o que sempre fez. Preenchida com `dados`, um segundo `actions/checkout` traz a branch para `dados-branch/`, e é lá que o coletor grava (`--dados`), o gerador lê (`--dados`) e o commit acontece (`git -C`).

A ordem de ativação — e ela importa:

1. mergear esta branch em `main`. Nada muda: a variável ainda não existe;
2. `bash ferramentas/migrar-dados.sh`. Cria a branch `dados` **localmente**, a partir do `dados/` commitado. Não empurra nada, não escreve um byte no diretório de trabalho, recusa rodar com mudança pendente e, na segunda vez, não faz nada;
3. `git push origin dados`;
4. definir `BRANCH_DADOS` = `dados` em *Settings > Secrets and variables > Actions > Variables*. **É aqui que a mudança liga**;
5. rodar *Coletar e publicar* pelo *Run workflow* e conferir que o commit do dia caiu na branch `dados`, e não em `main`;
6. só então tirar `dados/` de `main`, por PR: `git rm -r --cached dados` e `/dados/` no `.gitignore`. Antes de o passo 5 dar certo, não — o dado do dia ainda está ali;
7. criar o ruleset em `main`: PR obrigatório, check `ci`, sem force-push. A partir do passo 4 o bot não empurra mais em `main`, que é justamente o que faltava para isso ser possível;
8. localmente, `git worktree add dados dados`. A branch passa a aparecer como uma pasta `dados/` ao lado do código, e `python gerar_site.py` volta a funcionar sem argumento nenhum.

Para voltar atrás em qualquer ponto até o 5, apague a variável: no run seguinte tudo volta para `main`, e o `dados/` de lá continua onde estava.

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
