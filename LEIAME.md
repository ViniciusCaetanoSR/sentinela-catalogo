# Branch `dados`

Esta branch não tem código, não tem histórico em comum com `main` e não
recebe pull request: é só o estado que o bot escreve, um commit por coleta.
O que existe aqui é o que em `main` vivia em `dados/`.

| arquivo | o que é |
|---|---|
| `ultimo.json` | o snapshot do dia: contagens, viradas, invariantes |
| `atributos.json` | os atributos que ganham página, com as páginas permanentes |
| `lastmod.json` | hash de cada página no último build (é o que faz o sitemap não mentir) |
| `historico/AAAA-MM-DD.json` | um snapshot por dia — a janela de "o que mudou" |

Fora do git, porque são derivados e grandes: `completo.json` (~4,8 MB por
dia) e `bruto.zip`. O ZIP oficial de cada versão fica nas releases
`bruto-*` de `main`.

Por que separado de `main`: o bot commitava aqui todo dia, o que impede
exigir pull request em `main` (o `GITHUB_TOKEN` não passa por cima de um
ruleset) e faz toda branch de feature conflitar na linha que sempre
difere. Com a separação, `main` é só código.

Localmente, para ver esta branch como uma pasta `dados/` ao lado do
código: `git worktree add dados dados`.
