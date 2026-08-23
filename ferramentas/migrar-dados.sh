#!/usr/bin/env bash
#
# Prepara a branch órfã "dados" LOCALMENTE. Não toca a rede.
#
# O QUE ELE FAZ
#   Cria a branch "dados" com um único commit sem pai, cuja RAIZ é o
#   conteúdo de dados/ como está versionado no commit atual — ultimo.json,
#   atributos.json, lastmod.json e historico/ — mais um .gitignore, um
#   .gitattributes e um LEIAME.md próprios da branch. A raiz é o conteúdo,
#   e não uma pasta dados/ dentro dela, porque é assim que o workflow a
#   consome: um segundo actions/checkout com path: dados-branch, e daí
#   "--dados dados-branch" no coletor e no gerador.
#
# O QUE ELE NÃO FAZ
#   Não empurra nada — nem `git push`, nem nada que fale com o GitHub. Não
#   troca a branch em que você está, não mexe no índice e não escreve um
#   único byte no diretório de trabalho. E não liga a mudança: enquanto a
#   variável de repositório BRANCH_DADOS não existir, os workflows
#   continuam gravando dados/ em main e esta branch fica ali sem efeito
#   nenhum. A ordem completa de ativação está no CONTRIBUTING.md, seção
#   "Mover dados/ para a branch própria" — e sai impressa no fim daqui.
#
# POR QUE POR PLUMBING, E NÃO PELO `git checkout --orphan` DE MANUAL
#   O caminho do manual (checkout --orphan, `git rm -r --cached .`, add,
#   commit, voltar) passa por um estado em que o repositório está numa
#   branch sem commit, com o índice esvaziado e TODO arquivo do projeto
#   como não rastreado — e a volta para main falha com "untracked working
#   tree files would be overwritten" justamente por causa disso. Aqui o
#   diretório de trabalho nunca é lido nem escrito: o commit é montado num
#   índice temporário a partir de objetos que já existem no banco, e a
#   única mutação é criar a referência, no último comando. Falhar no meio
#   não deixa rastro para ninguém desfazer.
#
# Uso: ferramentas/migrar-dados.sh   (de qualquer pasta dentro do repo)

set -eu

BRANCH="dados"
# A pasta versionada que vira a raiz da branch.
ORIGEM="dados"

if [ "$#" -ne 0 ]; then
    echo "uso: ferramentas/migrar-dados.sh (sem argumentos)" >&2
    exit 2
fi

cd "$(git rev-parse --show-toplevel)"

# ------------------------------------------------------------- recusas

# Rodar duas vezes não pode duplicar nada nem sobrescrever uma branch que
# já saiu daqui e virou o estado de produção. A segunda vez não é erro:
# é "já está feito".
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    echo "A branch '$BRANCH' já existe aqui ($(git rev-parse --short "$BRANCH"))."
    echo "Nada a fazer. Para refazer do zero: git branch -D $BRANCH"
    exit 0
fi

# O commit é montado a partir do que está COMMITADO. Uma mudança pendente
# em dados/ entraria em silêncio na branch nova — ou, pior, não entraria e
# ninguém notaria. Melhor parar e deixar quem roda decidir.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "ERRO: há mudança não commitada no repositório." >&2
    echo "Este script migra o que está COMMITADO em $ORIGEM/; commite ou" >&2
    echo "descarte antes (em dados/, o de sempre: git checkout -- dados)." >&2
    exit 1
fi

atual="$(git rev-parse --abbrev-ref HEAD)"
if [ "$atual" != "main" ]; then
    echo "AVISO: você está em '$atual', não em main. A branch '$BRANCH' vai" >&2
    echo "sair do $ORIGEM/ DESTE commit, que pode não ser o de main." >&2
fi

arvore_origem="$(git rev-parse --verify --quiet "HEAD:$ORIGEM" || true)"
if [ -z "$arvore_origem" ]; then
    echo "ERRO: não existe $ORIGEM/ versionado em HEAD." >&2
    exit 1
fi

# commit-tree assina com o autor configurado; sem ele o erro sai críptico.
if ! git var GIT_AUTHOR_IDENT >/dev/null 2>&1; then
    echo "ERRO: configure user.name e user.email antes (git config --global)." >&2
    exit 1
fi

# ------------------------------------------------------- montar o commit

# Índice temporário: o índice de verdade não é tocado. mktemp cria o
# arquivo vazio e o git recusa um índice de 0 byte, então ele sai antes.
indice="$(mktemp)"
rm -f "$indice"
trap 'rm -f "$indice"' EXIT
export GIT_INDEX_FILE="$indice"

# O conteúdo de dados/ passa a ser a raiz. Nenhum blob é reescrito: são os
# mesmos objetos, só sob outra árvore.
git read-tree "$arvore_origem"

# Grava o texto do stdin como blob e o põe no índice com o nome dado.
acrescentar() {
    blob="$(git hash-object -w --stdin)"
    git update-index --add --cacheinfo "100644,$blob,$1"
}

# A branch precisa do seu próprio .gitignore: o do main protege
# "dados/completo.json" e "dados/bruto.zip", e aqui esses caminhos não
# existem mais — sem isto, o `git add .` do workflow versionaria 4,8 MB de
# completo.json por dia, que é exatamente o que a regra evita.
acrescentar .gitignore <<'FIM'
# Esta branch é só o estado do bot: a raiz é o que em main vive em dados/.
# Derivados e grandes, nunca versionados (em main são dados/completo.json
# e dados/bruto.zip, cobertos pelo .gitignore de lá).
completo.json
bruto.zip
*.zip
*.tmp
FIM

# Mesmo motivo: os atributos de dados/** do main não alcançam esta branch.
# eol=lf para o arquivo ser byte-idêntico gravado no Windows ou no runner;
# linguist-generated tira a branch da barra de linguagens e do diff.
acrescentar .gitattributes <<'FIM'
* text=auto eol=lf
# JSON escrito por máquina: fora do diff e fora da barra de linguagens.
* linguist-generated=true
atributos.json -diff
lastmod.json -diff
FIM

# Quem cair aqui pelo GitHub vê onze mil linhas de JSON e nenhum contexto.
acrescentar LEIAME.md <<'FIM'
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
FIM

arvore="$(git write-tree)"
mensagem="estado inicial dos dados"
commit="$(git commit-tree "$arvore" -m "$mensagem")"

unset GIT_INDEX_FILE
rm -f "$indice"
trap - EXIT

# A única mutação do script inteiro.
git branch "$BRANCH" "$commit"

# ----------------------------------------------------------- o que fazer

arquivos="$(git ls-tree -r --name-only "$BRANCH" | wc -l | tr -d ' ')"
cat <<FIM

Branch '$BRANCH' criada: $(git rev-parse --short "$BRANCH") ("$mensagem"),
$arquivos arquivos, sem pai. Nada foi empurrado e nada mudou em '$atual'.

Confira antes de publicar (o "--" separa a branch da pasta de mesmo
nome; sem ele o git não sabe qual das duas você quer):

    git log --oneline -1 $BRANCH --
    git ls-tree -r --name-only $BRANCH | head

E então, nesta ordem (a mesma do CONTRIBUTING.md, seção "Mover dados/
para a branch própria"):

  1. mergear em main a branch que trouxe o suporte a BRANCH_DADOS;
  2. git push origin $BRANCH
  3. definir a variável de repositório BRANCH_DADOS=$BRANCH em
     Settings > Secrets and variables > Actions > Variables;
  4. rodar 'Coletar e publicar' à mão e conferir que o commit do dia
     caiu em '$BRANCH', e não em main;
  5. tirar dados/ de main, por PR: git rm -r --cached dados e /dados/
     no .gitignore (só depois do passo 4 dar certo);
  6. criar o ruleset em main: PR obrigatório, check 'ci', sem
     force-push - a partir do passo 4 o bot não empurra mais em main;
  7. localmente: git worktree add dados $BRANCH

Até o passo 3 nada muda: com a variável vazia os workflows continuam
gravando dados/ em main, exatamente como hoje.
FIM
