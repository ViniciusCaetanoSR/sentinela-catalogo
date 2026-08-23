# Histórico de mudanças

A partir da 0.1.0 este projeto usa [SemVer](https://semver.org/lang/pt-BR/), e a versão vive em `comum.__version__` — um teste exige que ela seja a primeira deste arquivo, para não existir tag sem entrada aqui. O formato é o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) simplificado: cada versão é agrupada pela parte do sistema que mudou, não pelo tipo da mudança.

## 0.1.0 — 2026-08-23

Primeira versão marcada. O site está no ar desde 2026-08-20; esta entrada cobre o caminho do primeiro dia até aqui — de um script que baixava e publicava para algo com portão de sanidade antes de qualquer escrita, suíte que não toca a rede, CI em todo push e um site que fecha.

### Coletor

- **Portão de sanidade** antes de gravar qualquer arquivo: `versao` presente, piso absoluto por contagem e queda máxima de 10% contra o snapshot anterior (base rolante). Sem ele, um ZIP válido com `listaNcm` vazia derrubava o site de 918 páginas para 5, avisava o Google disso pelo sitemap e commitava tudo com exit 0.
- **Válvula do portão** (`SENTINELA_ACEITAR_QUEDA=1`, ou o input `aceitar_queda` do workflow): a queda rolante vira aviso e o snapshot sai marcado com `portao_ignorado`. Pisos absolutos e `versao` continuam fatais — a válvula aceita um catálogo menor, não um catálogo vazio.
- **Validação de forma** (`validar_forma`): tipo errado no arquivo oficial passa a ser erro. `obrigatorio` como `"false"` zerava as viradas e publicava "nada agendado" com exit 0; `obrigatorios` entrou no piso e na base rolante.
- **Retry que cobre a leitura do corpo** — `IncompleteRead` e `ConnectionReset` não são `URLError` e escapavam do retry e do `main()`, levando o dia embora com traceback. Cinco tentativas, esperas de 10 a 180 s, 408 e 429 honrando `Retry-After`; o resto dos 4xx continua sem repetição.
- **Data inválida isolada não derruba o dia**: entra em `datas_invalidas` e é logada com NCM e atributo; acima de 0,1% dos vínculos o portão recusa a colheita.
- **Escrita atômica** em tudo (`.tmp` + `os.replace`) e reescrita só quando o conteúdo muda de verdade: `atualizado_em` saiu do catálogo, que era reescrito com 1 MB idêntico todo dia.
- **Snapshot no schema 2**: a virada carrega só o que é do vínculo, o que é do atributo vai uma vez para um mapa e `ncms_afetadas` some. Uma virada em massa — o `ATT_15540` (cClassTrib) é opcional em todas as 10.516 NCMs — passa a crescer linearmente: ~13 MB por dia viraram ~1,8 MB.
- Nova contagem `fim_vigencia_passado_opcional` e a invariante "nenhum fim de vigência passado ainda opcional", que é a hipótese central do produto sendo conferida todo dia.
- **`apurar()` puro, `gravar()` e `coletar()`**, com `argparse`: `--de-arquivo` apura um ZIP do disco sem tocar a rede (é o que permite reapurar um dia perdido e renderizar sem coleta), mais `--dados`, `--referencia` e `--aceitar-queda`.
- O ZIP oficial fica em `dados/bruto.zip`; tetos de tamanho no download e na descompactação; a assinatura `PK` é aceita quando o `Content-Type` mente; sem `tzdata` o fuso cai para UTC-3 fixo, com aviso.
- **`comum.py`** concentra o que os scripts dividiam por cópia: `Caminhos`, escrita atômica, o único leitor de `config.json`, o User-Agent e `__version__`.

### Gerador

- **Uma página por NCM** (~10.600), com índice por capítulo, páginas de atributo e de órgão — e o corte das quase-duplicatas, que evita publicar 586 páginas com a mesma prosa de 31 caracteres.
- **`lastmod.json` honesto**: o hash de cada página é assinado pelos *dados*, não pelo HTML. A contagem de dias vive no corpo e carimbava data nova em toda página com virada, todo dia; a chave `__templates__` guarda o hash dos templates e é o que distingue um rebuild de um dia normal.
- Estáticos com hash de conteúdo no nome e **uma folha só** (as `@font-face` na frente do estilo), no lugar de 16,5 KB repetidos em cada página.
- Feed com `pubDate` por item (a primeira vez em que aquela virada foi vista no histórico), índice de sitemaps com a data máxima do bloco, 404 com `noindex` e sem canonical.
- **Modo lote** acima de 50 NCMs no mesmo par (atributo, data): uma linha na home, um item no feed e um em "o que mudou"; a página de cada NCM continua individual.
- **Páginas de atributo que não somem**: `paginas_permanentes` guarda a união de tudo o que já foi publicado, e a página sobrevive ao fim da virada que a criou. Antes, uma URL anunciada por semanas no sitemap virava 404 no dia seguinte ao corte.
- "O que mudou" ganhou **prazos alterados**, e a tabela da NCM ganhou a situação **prazo vencido**.
- Dados estruturados completos: `Dataset` com `distribution` (feed, JSON e CSV), `@graph` com `BreadcrumbList` e `WebPage`, e um `dateModified` que é a mesma data do `lastmod` do sitemap; toda data visível em `<time datetime>`.
- Estado da geração num **`Build`** explícito, `--raiz`/`--dados`/`--saida`/`--base-path` na linha de comando, e `esc()` no único ponto de interpolação de dado oficial que ainda saía cru.
- Arquivos novos do site: `status.json` (prova de vida do build), `dados/viradas.{json,csv}` (dados abertos) e `mudancas.txt`.
- **`indexnow.py`**: `keyLocation` com o `base_path` (apontado para a raiz do host, dava 422 em Pages de projeto), envio em lotes de 10 mil URLs e sem poda por contagem. **`servir.py`**: preview local fiel ao Pages, com o prefixo e o 404.

### Site

- O **prazo é recalculado no navegador**, com a data local de quem lê — inclusive "prazo vencido há N dias", que o build nunca escreve. Os textos vivem numa tabela única, copiada para o JS e conferida por teste; sem JS fica o texto do build.
- **Faixa de dado velho**: passados dois dias do snapshot, toda página avisa de quando é o dado. O vigia avisa quem mantém; a faixa avisa quem lê.
- **"Ir para a NCM"** na home, no índice e no 404: `84151090`, `8415.10.90` e `8415 10 90` levam ao mesmo lugar, 4 a 7 dígitos levam ao capítulo e, sem JS, o envio cai no índice por capítulo.
- Acessibilidade e impressão: folha `@media print`, links sublinhados na prosa (o contraste sozinho era 2,8:1, abaixo do mínimo), `role` nas tabelas para o cartão do celular manter a semântica, paginação com `aria-current`, contagem da home anunciada por leitor de tela.
- Favicon PNG e `apple-touch-icon`, `theme-color`, e as licenças OFL das fontes auto-hospedadas publicadas junto com elas.

### CI/CD

- **`ci.yml`** (novo): em todo push e pull request, `compileall` e a suíte em Python 3.9 e 3.12, sem `pip`; um job de lint à parte com `ruff check`, `ruff format --check` e cobertura no resumo da run. Antes, o CI só disparava em push de `coletor.py` e nunca via o gerador nem os templates.
- **`coletar.yml`**: segundo cron de resgate às 10:00 BRT, que se pula sozinho quando o dia já foi coletado; a coleta deixou de depender da suíte (teste vermelho não custa mais o dia, e o portão protege a gravação); `concurrency`; artifact `snapshot-<data>`; release `bruto-<versao>` quando o conteúdo muda; cache do `completo.json`; issue por label, fechada sozinha quando a coleta seguinte passa.
- **`render.yml`** (novo): trocar um CSS não pede mais uma ida à Receita — o render roda do `completo.json` em cache ou reapurando o ZIP da última release.
- **`vigia.yml`** (novo): homem morto diário — snapshot com mais de dois dias abre issue.
- **Conferência pós-deploy**: o `status.json` é baixado da URL pública e tem de ser o desta run (data certa, piso de páginas), o `sitemap.xml` tem de trazer `<loc>` e um caminho inexistente tem de dar 404. Só depois dela vem o ping opcional no monitor externo (`HEALTHCHECKS_URL`), que é o único alerta que sobrevive ao GitHub desligar os crons deste repositório.
- Dependabot num grupo único e com mensagem no estilo do projeto.
- A mudança de `dados/` para uma **branch órfã** está pronta e **desligada**: os workflows leem `vars.BRANCH_DADOS` e, enquanto ela não existir, fazem exatamente o que faziam. `ferramentas/migrar-dados.sh` cria a branch localmente, sem empurrar nada.

### Testes

- De zero a **358 testes em cerca de 7 s**, verdes nos dois interpretadores e **sem tocar a rede** — `tests/apoio.py` faz `urlopen` levantar em todo módulo e monta o ambiente num diretório temporário pelo mesmo caminho da produção (`apurar` + `gravar`).
- `test_integridade` gera o site inteiro a partir da fixture e confere todo link interno, todo `<loc>` do sitemap, a estrutura de cada página (um `<h1>`, canonical igual a `og:url`, JSON-LD parseável) e que nenhum placeholder sobrou.
- Fixture hostil (`ATT_HOSTIL`: nome com `<`, `>` e aspas, definição com `</script>`, orientação com `{{chave}}`, órgão com tag dentro): nada dela pode chegar cru a nenhum HTML, ao feed, ao CSV ou aos sitemaps.
- Cobertura das fronteiras que doem: cada piso do portão, a queda exata de 10%, o retry e o backoff, a janela de 30 dias do histórico, o lastmod que só carimba o que mudou, o modo lote, o prazo vencido, o IndexNow e o preview local.
- **Golden files** (`tests/golden/`) de três páginas — a home, uma NCM com virada e um atributo com virada —, comparadas caractere a caractere, com o hash dos estáticos normalizado. É o que pega a regressão de marcação (um `<h1>` que vira `<h2>`, um `aria-label` que some) que fecha o site do mesmo jeito e passaria despercebida.

### Documentação

- README refeito: pipeline real, arquitetura, como rodar sem rede, recuperar um dia perdido, render sem coleta, monitor externo, armadilhas do endpoint (todas confirmadas contra o servidor) e o que acontece **depois dos 60 dias**.
- `CONTRIBUTING.md` (regras de código, testes, por que `dados/` nunca entra em branch de feature, como publicar uma versão), `SECURITY.md`, `LICENSE` MIT puro e `LICENSE-DADOS.md` à parte — com o apêndice dentro, o GitHub não reconhecia a licença.
- `pyproject.toml` com `ruff` (92 colunas, alvo 3.9), `.editorconfig` e este `CHANGELOG.md`.
- Os rascunhos de divulgação saíram do repositório: avaliação franca de terceiros não pertence a um repositório público.
