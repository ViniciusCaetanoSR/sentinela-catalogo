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
- **Constantes nomeadas** para todo número que signifique alguma coisa (`PISO`, `POR_SITEMAP`, `TETO_INDEXNOW`).
- Estilo mecânico é do `ruff` (`pyproject.toml`): linha de 92 colunas, aspas duplas, imports ordenados.

```bash
python -m pip install ruff coverage       # só para desenvolver
python -m ruff check .
python -m ruff format --check .            # ou `ruff format .` para aplicar
```

## Testes

- **Obrigatórios e rápidos.** A suíte inteira roda em cerca de 2 s e **nunca toca a rede** — `tests/apoio.py` faz `urlopen` levantar em todo módulo de teste (`proibir_rede()` no `setUpModule`), o coletor é testado com respostas falsas e a fixture em `tests/fixtures/`. Um teste que precise de internet fica vermelho, não lento.
- **Ambiente de teste é `apoio.ambiente(tmp)`**: devolve um `Caminhos` num diretório temporário com templates, fontes e `config.json`; `apoio.montar_dados()` produz `dados/` pelo mesmo caminho da produção (`apurar` + `gravar`). Não remonte o snapshot à mão.
- Todo bug corrigido ganha um teste com nome que diz o que ele prova (`test_colheita_degenerada_nao_grava_nada`).
- `tests/test_integridade.py` gera o site inteiro a partir da fixture e confere todo link interno. Se você mexeu em template, é ele que vai te pegar.

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

## Preview local

```bash
python gerar_site.py      # usa o dados/ que está no disco; não toca a rede
python servir.py          # http://localhost:8000/<base_path>/
```

`servir.py` tira o `base_path` do caminho e serve `404.html` para o que não existe, como o GitHub Pages faz. Abrir `site/index.html` direto no navegador não funciona: todo link interno carrega o prefixo.

Para ter um `dados/completo.json` local sem bater na Receita, pegue o ZIP do release `bruto-<versao>` mais recente e rode `python coletor.py --de-arquivo <zip>` — ou rode `python coletor.py` uma vez (é um download de ~500 KB, o endpoint é público). Nos dois casos, depois: `git checkout -- dados` (o `completo.json` e o `bruto.zip`, que o git ignora, ficam). Para não tocar no `dados/` do repositório de jeito nenhum, use `--dados <outra pasta>` e `python gerar_site.py --raiz`/`--saida`.
