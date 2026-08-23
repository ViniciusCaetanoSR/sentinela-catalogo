# Sentinela do Catálogo

[![CI](https://github.com/ViniciusCaetanoSR/sentinela-catalogo/actions/workflows/ci.yml/badge.svg)](https://github.com/ViniciusCaetanoSR/sentinela-catalogo/actions/workflows/ci.yml)
[![Coletar e publicar](https://github.com/ViniciusCaetanoSR/sentinela-catalogo/actions/workflows/coletar.yml/badge.svg)](https://github.com/ViniciusCaetanoSR/sentinela-catalogo/actions/workflows/coletar.yml)

Monitora a relação pública de **atributos por NCM** do Catálogo de Produtos do Portal Único (Siscomex) e publica, todo dia, quais atributos hoje opcionais têm data marcada para virar obrigatórios — e portanto quais produtos serão desativados se não forem preenchidos até lá.

**Isto não é um produto. É um teste de demanda de 60 dias**, com critério de morte escrito antes de começar. Primeira coleta publicada em 2026-08-20; decisão por volta de 2026-10-19. O critério em si (o que conta como sinal de demanda e o que acontece se ele não aparecer) está no hub do projeto — a ser transcrito aqui quando o teste fechar.

Enquanto o teste corre, o código é mantido como se fosse durar anos — porque, se der certo, vai.

## A regra

```
obrigatorio == false  AND  dataFimVigencia >= hoje (America/Sao_Paulo)
```

O corte é `>=`, não `>`: no dia exato da virada o vínculo tem de continuar aparecendo. É o dia mais acionável para quem mantém o catálogo.

## Como roda

```
comum.py        o que os scripts dividem: caminhos, escrita atômica, config.json

coletor.py      baixa o ZIP oficial e apura (ou lê um ZIP do disco: --de-arquivo)
                lê     dados/ultimo.json              (base rolante do portão)
                       dados/atributos.json           (páginas permanentes de ontem)
                grava  dados/bruto.zip                o ZIP como veio (NÃO versionado)
                       dados/ultimo.json              snapshot do dia
                       dados/historico/AAAA-MM-DD.json
                       dados/atributos.json           os atributos que ganham página
                       dados/completo.json            mapa NCM->atributos (NÃO versionado)

gerar_site.py   sem rede
                lê     dados/ultimo.json, atributos.json, completo.json,
                       dados/historico/*.json         ("o que mudou em 30 dias")
                       dados/lastmod.json             (hash por página do último build)
                       config.json, templates/, fontes/
                grava  site/                          HTML estático, sitemaps, feed,
                                            dados/viradas.{json,csv}
                       site/status.json               prova de vida do build (fora
                                                       do sitemap e do lastmod)
                       site/mudancas.txt              URLs que mudaram neste build
                       dados/lastmod.json             (versionado: é o que faz o
                                                       sitemap não mentir)

indexnow.py     lê     site/mudancas.txt + config.json -> POST no IndexNow
                                                        (em lotes de 10 mil URLs)
servir.py       serve  site/ em localhost como o Pages serviria
```

Quatro workflows no GitHub Actions:

| workflow | quando | o que faz |
|---|---|---|
| **CI** (`ci.yml`) | todo push e pull request | `compileall` + suíte em Python 3.9 e 3.12; job de lint com `ruff` e cobertura (o único lugar com `pip`) |
| **Coletar e publicar** (`coletar.yml`) | 06:00 BRT, resgate às 10:00 BRT, manual | testes → coleta → commit de `dados/` → release do bruto → render → commit do `lastmod` → Pages → conferência do que ficou no ar → ping do monitor externo → IndexNow; abre/fecha issue `coleta-falhou` |
| **Renderizar** (`render.yml`) | push em `main` que toca `templates/`, `fontes/`, `gerar_site.py`, `config.json`; manual | render e deploy **sem coleta**, a partir do `completo.json` em cache — ou reapurado do ZIP da última release; mesma conferência pós-deploy |
| **Vigia** (`vigia.yml`) | 12:00 BRT | homem morto: se o snapshot tem mais de dois dias, abre issue `vigia` |

Sem servidor, sem banco, sem dependências: **só a biblioteca padrão do Python**, 3.9+ (o piso é o `zoneinfo`).

## Rodar localmente

```bash
python coletor.py                         # baixa o ZIP oficial e apura (bate na Receita)
python coletor.py --de-arquivo bruto.zip  # apura um ZIP já baixado — sem rede
python gerar_site.py                      # gera site/ a partir de dados/ — sem rede
python servir.py                          # http://localhost:8000/sentinela-catalogo/
python -m unittest discover -s tests -v   # ~300 testes em cerca de 4 s, sem rede
```

Os dois scripts principais aceitam argumentos para não tocar no `dados/` e no `site/` do repositório:

| script | argumento | o que faz |
|---|---|---|
| `coletor.py` | `--de-arquivo ZIP` | apura o ZIP do disco em vez de baixar (o asset de uma release `bruto-*`, ou o `dados/bruto.zip` da última coleta) |
| | `--dados DIR` | grava em `DIR` em vez de `dados/` |
| | `--referencia AAAA-MM-DD` | fixa o "hoje" da regra; padrão é hoje em Brasília |
| | `--aceitar-queda` | a válvula do portão, igual a `SENTINELA_ACEITAR_QUEDA=1` |
| `gerar_site.py` | `--raiz DIR` | outro repositório (`dados/`, `templates/`, `fontes/`, `config.json` de lá) |
| | `--dados DIR` | lê os dados daqui e o resto da raiz — é como o workflow aponta para o checkout da branch `dados` sem mover templates nem `config.json` |
| | `--saida DIR` | grava o site em `DIR` em vez de `site/` |
| | `--base-path /x` | sobrescreve o `base_path` do `config.json` (vazio para servir na raiz) |

Para ter um `dados/completo.json` local sem bater na Receita: `python coletor.py --de-arquivo <zip da release bruto-*>` e depois `git checkout -- dados` (só o `completo.json` e o `bruto.zip`, que o git ignora, ficam).

`servir.py` existe porque o site é gerado para viver sob o `base_path` (`/sentinela-catalogo/`): abrir `site/index.html` direto no navegador quebra todo link interno. O script tira o prefixo do caminho e serve `404.html` para o que não existe — o mesmo que o Pages faz.

No **Windows**, `pip install tzdata` para o fuso oficial: sem o pacote, o `zoneinfo` não encontra `America/Sao_Paulo` e o coletor cai num UTC-3 fixo com aviso (correto desde que o Brasil aboliu o horário de verão, em 2019, mas é bom saber que é um fallback).

Lint, só para quem desenvolve (o runtime nunca precisa disso):

```bash
python -m pip install ruff coverage
python -m ruff check .
python -m ruff format --check .
```

## Arquitetura

Dois scripts de verdade, dois auxiliares e um módulo comum; entre os scripts, nenhuma abstração além de arquivos JSON:

- **`comum.py`** é o que eles dividem: `Caminhos` (todo arquivo e pasta, derivados de uma raiz — é o que deixa os testes apontarem tudo para um diretório temporário), a escrita atômica (`.tmp` + `os.replace`, para nunca deixar um JSON pela metade em `dados/`), o único leitor de `config.json` e as funções de URL. Não toca a rede nem conhece o formato do catálogo.
- **`coletor.py`** é o único que toca a rede. `apurar()` é pura — do JSON carregado ao que será gravado, com a validação de forma e o portão de sanidade no meio — e `gravar()` escreve; `coletar()` liga as duas, baixando ou lendo um ZIP do disco (`--de-arquivo`).
- **`gerar_site.py`** é uma função pura de `dados/` + `templates/` para `site/`. Roda sem rede, determinístico: o mesmo dado gera os mesmos bytes. O estado de uma geração vive num `Build` passado explicitamente a cada função. Por isso existe `lastmod.json`: hash de cada página no último build, para que o sitemap só carimbe data nova no que mudou de fato. O arquivo guarda também, sob `__templates__`, o hash de `templates/` e de `fontes/fontes.css`: quando ele muda (ou quando o `lastmod.json` não existe) o build é um *rebuild* — o HTML de toda página mudou sem que o dado tenha mudado — e `mudancas.txt` leva só a raiz. Fora disso leva todas as URLs que mudaram, sem teto.
- **`site/status.json`** é a prova de vida do build, e o único arquivo do site deliberadamente volátil: traz `data_referencia`, `versao`, `gerado_em`, `paginas`, `viradas`, `proximo_corte` e `schema`. Fica **fora do sitemap e do `lastmod.json`** — `gerado_em` muda a cada build e carimbaria data nova todo dia numa URL que ninguém precisa indexar. É o que o passo *Conferir o que foi publicado* baixa da URL pública depois do deploy, para exigir que o site no ar seja o build desta run (a data certa e ao menos 5 000 páginas) em vez de um artifact velho ou um `base_path` errado.
- **`indexnow.py`** e **`servir.py`** são invólucros finos. O primeiro avisa os buscadores das URLs em `mudancas.txt`; o segundo é preview.
- **`dados/`** é o estado. Versionado em `main` por enquanto, escrito só pelo bot (ver [CONTRIBUTING.md](CONTRIBUTING.md) sobre por que nunca entra em branch de feature, e sobre a branch própria que já está pronta e desligada). `completo.json` e `bruto.zip` ficam de fora por tamanho e churn.

## O que o site publica

| páginas | o quê |
|---|---|
| ~10.600 | uma por NCM com atributo exigido, com a tabela completa e as opções válidas |
| ~100 | índice por capítulo da nomenclatura (o caminho de rastreio até as NCMs) |
| ~390 | uma por atributo que tem conteúdo próprio para dizer |
| ~17 | uma por órgão anuente |

Três coisas acontecem no navegador (`templates/app.js`), não no build, e todas degradam para o HTML do build quando o JS não roda:

- **O prazo é recalculado com o relógio de quem lê.** O build é de manhã, em Brasília; cada "faltam N dias", cada barra de prazo e o número grande da home carregam `data-corte` (a data da virada) e são refeitos com a data local do navegador — inclusive "prazo vencido há N dias", que o build nunca escreve. Os textos vivem numa tabela única (`gerar_site.TEXTOS_PRAZO`) copiada literalmente para o JS, e um teste confere que as duas cópias são iguais.
- **Dado velho tem aviso.** Toda página leva `data-referencia` no `<body>`; se o snapshot tiver mais de dois dias, uma faixa no topo diz "estes dados são de DD/MM/AAAA". O `vigia.yml` avisa quem mantém; a faixa avisa quem lê.
- **"Ir para a NCM"** (home, `/ncm/` e 404) é um form sem backend: `84151090`, `8415.10.90` e `8415 10 90` levam à mesma página; 4 a 7 dígitos levam ao capítulo. Sem JS o envio cai no índice por capítulo. A página de erro também reconhece `/ncm/84151090/` e redireciona para a forma pontuada.

O snapshot do dia também sai como **dados abertos**, em `/dados/viradas.json` (o `ultimo.json` normalizado, sem os campos voláteis da execução) e `/dados/viradas.csv` (uma virada por linha: NCM, atributo, nome, órgãos, data, vigência, modalidade), linkados no rodapé da tabela da home e declarados como `distribution` do `Dataset` em JSON-LD, ao lado do feed. Cada página de NCM e de atributo leva, no JSON-LD, um `WebPage.dateModified` que é a **mesma** data do `lastmod` do sitemap — o hash da página é calculado antes do HTML justamente para isso —, e toda data visível sai em `<time datetime>`.

Um atributo só ganha página própria se tiver algo próprio a dizer. Os que valem para **uma única NCM** e cuja prosa é boilerplate repetido — 586 atributos chamados "Destaque", com a mesma orientação de 31 caracteres — não ganham: o conteúdo deles aparece dentro da página da NCM, que é onde ele sempre pertenceu. Sem esse corte, dois terços do site eram quase-duplicatas e as 888 páginas de atributo produziam 118 títulos distintos.

A exceção ao corte é a virada: todo atributo com virada agendada, e todo atributo citado por uma NCM com virada, ganha página enquanto a virada dura. E **página que já existiu continua existindo**: `dados/atributos.json` guarda em `paginas_permanentes` a união de tudo o que já foi publicado, e o coletor a herda de um dia para o outro. Sem isso, no dia seguinte ao corte a página sumia — 404 numa URL que o sitemap anunciou por semanas. Só some a página de um atributo que a Receita removeu do arquivo: sem dado não há o que publicar.

### Modo lote

Hoje o maior grupo de viradas com o mesmo atributo e a mesma data tem 4 NCMs. Mas 32 atributos opcionais alcançam mais de 50 NCMs, e um deles — `ATT_15540`, o cClassTrib da reforma tributária — é opcional em **todas as 10.516**. Uma `dataFimVigencia` nele seriam 10.516 viradas no mesmo dia: 10.516 linhas na home, 10.516 itens no feed, uma home de 4,7 MB. Por isso existe o limiar (`LIMIAR_LOTE = 50`): quando um mesmo par (atributo, data) passa de 50 NCMs, ele vira **uma linha** na home ("cClassTrib vira obrigatório em 01/01/2027 para 10.516 NCMs", com link para a página do atributo), **um item** no feed e **um item** em "o que mudou". A página de cada NCM continua individual; a do atributo lista as 60 primeiras NCMs e remete ao índice por capítulo. Abaixo do limiar nada muda.

### Prazo vencido

A regra das viradas é `dataFimVigencia >= hoje`. O complemento dela — a data passou e o vínculo **continua** `obrigatorio: false` — é a hipótese central do produto falhando: a Receita não trocou o campo na data. Nunca foi verificada. Se acontecer, a tabela da NCM mostra **prazo vencido em DD/MM/AAAA** em vez de "opcional", o coletor conta em `fim_vigencia_passado_opcional`, a invariante "nenhum fim de vigência passado ainda opcional" falha e o log lista os pares (NCM, atributo) para alguém conferir na fonte.

## Portão de sanidade

`coletor.py` **recusa gravar** se a colheita vier degenerada: `versao` ausente, `detalhesAtributos` vazio, contagens abaixo de um piso absoluto, ou queda de mais de 10% em relação ao snapshot anterior.

Isso não é paranoia. Sem o portão, um ZIP válido com `listaNcm` vazia derrubava `dados/atributos.json` de 1 MB para 85 bytes, o site de 918 páginas para 5, o sitemap avisava o Google disso, e o workflow commitava e publicava tudo **com exit 0**. O endpoint ignora `?data=`: o dia não volta.

As invariantes de *forma* (`versao` é string, `detalhes == distintos`, nenhum registro descartado, `obrigatorio` é booleano) são conferidas a cada execução e testadas em `tests/`. As de *magnitude* ficam no portão, com base rolante — uma tabela de números congelados vira ruído em dois dias.

### Válvula do portão

Um dia a Receita vai encolher a relação de verdade — uma reforma tira um órgão anuente, uma NCM inteira sai da nomenclatura — e a queda de 10% vai ser legítima. Para esse dia existe a válvula:

- localmente: `SENTINELA_ACEITAR_QUEDA=1 python coletor.py`;
- no GitHub: rodar **Coletar e publicar** à mão com o input `aceitar_queda` marcado.

Com a válvula aberta, a queda rolante vira aviso e o snapshot sai marcado com `portao_ignorado: true`. Os **pisos absolutos e a `versao` continuam fatais**: a válvula aceita um catálogo menor, não um catálogo vazio.

## Monitor externo

Todo alerta deste repositório — a issue do job `avisar`, o `vigia.yml` — depende de o GitHub continuar rodando workflows aqui, e é exatamente isso que para quando o cron é desativado por inatividade: o vigia compartilha o modo de falha que vigia. O monitor externo funciona ao contrário — avisa quando o ping **não** chega. Para ligar: crie um check em [healthchecks.io](https://healthchecks.io) com período de 1 dia e graça de 12 h, copie a URL de ping e guarde-a em *Settings > Secrets and variables > Actions* como `HEALTHCHECKS_URL`. O último passo da publicação faz um `curl` nela; sem o secret o passo é pulado e nada muda.

O ping é o **último** passo da publicação, depois da conferência: se o que ficou no ar não bate, ele nem chega a ser feito — e o monitor reclama sozinho. O que ele cobre e o `vigia.yml` não é o silêncio total: o cron do vigia desativado junto com o da coleta, o repositório arquivado, a conta sem minutos de Actions. O que ele **não** substitui: uma coleta que falha barulhentamente (é o job `avisar` quem abre a issue) nem um dado degenerado (é o portão de sanidade quem recusa gravar). Ele responde a uma pergunta só, e é a que ninguém mais faz: *o dia de hoje chegou ao fim da publicação?*

## Recuperar um dia perdido

O endpoint não serve versões passadas, então cada dia que a coleta perde é um buraco definitivo em `dados/historico/` — a não ser que alguém tenha guardado o arquivo. O workflow guarda em dois lugares:

1. **Artifact `snapshot-<data>`** da run, por 14 dias: `dados/historico/*.json` e `dados/bruto.zip` como estavam no disco do runner, mesmo quando o job caiu no meio. Baixe pela aba *Actions* da run (ou `gh run download <id> -n snapshot-<data>`).
2. **Release `bruto-<versao>`**: o ZIP oficial, guardado toda vez que o `sha256` do JSON descompactado muda. A tag é a `versao` que a Receita declara; as notas trazem o sha256 e a data da coleta. É a única cópia de longo prazo do arquivo bruto.

Se a coleta das 06:00 falhou e a de resgate das 10:00 passou, a issue `coleta-falhou` é fechada sozinha com o comentário "coleta de `<data>` publicada". Se as duas falharam, a issue fica aberta e o dia precisa ser recuperado à mão a partir do artifact — ou aceito como perdido, que é o que acontece com a maioria dos feriados do servidor.

## Render sem coleta

Trocar um CSS, um template ou o `config.json` não pede dado novo — pede um site novo. O workflow **Renderizar** dispara sozinho em push em `main` que toque `templates/`, `fontes/`, `gerar_site.py` ou `config.json` (e manualmente, pelo *Run workflow*), restaura o `completo.json` da última coleta do cache do Actions, roda o gerador e publica. A Receita não é consultada.

Se o cache não existir (repositório novo, ou cache expirado por inatividade de uma semana), o job baixa o ZIP da última release `bruto-*` e reapura com `python coletor.py --de-arquivo`, sem tocar a Receita; o `completo.json` sai disso e o resto de `dados/` é restaurado do git. Só quando também não há release o job falha, com a mensagem "rode o workflow Coletar". Uma coleta regrava o cache e, quando o conteúdo muda, a release.

## A branch `dados`

Cada coleta deixa dois commits em `main` — o snapshot e o `lastmod` —, e o preço disso é alto: `main` não pode exigir pull request, porque o `GITHUB_TOKEN` não passa por cima de um ruleset e a coleta pararia na manhã seguinte; e toda branch de feature conflita na linha que sempre difere.

A saída está **pronta e desligada**: uma branch órfã `dados`, sem código e sem histórico em comum com `main`, com o conteúdo de `dados/` na raiz. Os três workflows que tocam o dado — *Coletar*, *Renderizar* e *Vigia* — leem a variável de repositório `BRANCH_DADOS` e, **enquanto ela não existir, nada muda** — o dado continua em `main`, exatamente como hoje. Preenchida com `dados`, um segundo `actions/checkout` traz a branch para `dados-branch/`, e é de lá que o coletor grava (`--dados`), o gerador lê (`--dados`) e o commit sai (`git -C`). `ferramentas/migrar-dados.sh` cria a branch localmente, a partir do `dados/` que está em `main`, sem empurrar nada e sem tocar no diretório de trabalho. A ordem de ativação, passo a passo, está em [CONTRIBUTING.md](CONTRIBUTING.md#mover-dados-para-a-branch-própria).

## Armadilhas do endpoint

Todas confirmadas contra o servidor real. Estão tratadas no código; não as remova achando que são paranoia.

1. **`?perfil=PUBLICO` é obrigatório.** Sem ele vem `307` e, se o redirect não for seguido, 304 bytes de HTML no lugar do ZIP.
2. **`Accept: application/json` devolve `406`.** O endpoint só serve `application/zip`. É o header que qualquer um poria num coletor de "JSON". O coletor manda `Accept: */*` e não repete tentativa em 4xx (exceto 408 e 429, que são transitórios por definição).
3. **`dataFimVigencia` tem duas convenções de ausência no mesmo arquivo:** em `listaAtributos` a chave é omitida; em `detalhesAtributos` vem como `""`. Comparar string vazia como data inverte o filtro.
4. **Os bytes do ZIP mudam a cada requisição** (o mtime interno é o instante da geração). Para detectar mudança, hasheie o JSON descompactado ou compare `versao` — nunca o ZIP.
5. **O nome do arquivo interno muda todo dia** (`ATRIBUTOS_POR_NCM_AAAA_MM_DD.json`). Use `namelist()[0]`.
6. **`?data=AAAA_MM_DD` é silenciosamente ignorado.** Não existe histórico oficial — inclusive um valor inválido devolve o arquivo de hoje sem erro. Por isso `dados/historico/` e o release `bruto-<versao>` existem.
7. 493 KB comprimidos viram 16,5 MB; pico de ~200 MB no `json.loads`. O `completo.json` derivado tem ~4,8 MB (~120 KB comprimidos).
8. Sem `Content-Length` (chunked). Sem rate limit observado — ainda assim, colete 1×/dia.
9. **Página de manutenção servida com `200`** vira `BadZipFile` sem explicação. O coletor confere o `Content-Type` (e a assinatura `PK` dos primeiros bytes) antes de tentar descompactar.

## Por que guardamos o histórico

Não é fosso — o campo `dataFimVigencia` é aviso público, qualquer um lê. É **suprimento de conteúdo**: as viradas chegam em lotes e a lista esvazia entre eles. Sem o arquivo local não existe "o que mudou nos últimos 30 dias", e a página fica vazia. Custo marginal zero.

A janela é de 30 **dias**, não dos 30 últimos arquivos: um dia perdido por falha de rede alargaria o período em silêncio.

Cada snapshot declara o `schema` do seu formato (`coletor.SCHEMA`, hoje **2**). O gerador lê os formatos 1 e 2 e ignora, com aviso, o que vier acima do que conhece — o histórico carrega os dois lado a lado. No schema 1 cada virada repetia o nome e os órgãos do atributo e o snapshot trazia a ficha inteira de cada NCM afetada, o que crescia com o quadrado de uma virada em massa (~13 MB por dia no caso do cClassTrib). No 2 a virada carrega só o que é do vínculo (NCM, atributo, datas, modalidade), o que é do atributo vai uma vez para o mapa `atributos`, e a ficha da NCM é montada pelo gerador a partir de `completo.json` (que também leva `schema` e o fim de vigência de cada vínculo) — o mesmo caso cai para ~1,8 MB.

## Configuração

`config.json` — todas as chaves são opcionais, mas veja a coluna da direita:

| chave | padrão | o que faz se faltar |
|---|---|---|
| `base_url` | `""` | canonical, `og:url`, sitemap e feed saem com caminho relativo |
| `base_path` | `""` | **obrigatório em Pages de repositório de projeto.** O site é servido sob `/<repo>/`; sem isto todo link interno dá 404 |
| `contato_email` | `""` | sem captura por `mailto:` — o teste não produz métrica |
| `form_embed_url` | `""` | usa o `mailto:`; se preenchido, tem precedência e embute um iframe |
| `goatcounter_code` | `""` | sem analítica; zero cadastro não distingue "ninguém quer" de "ninguém viu" |
| `dominio` | `""` | sem `CNAME`, ou seja, sem domínio próprio |
| `indexnow_key` | `""` | sem arquivo de chave; o passo de ping do workflow roda, mas `indexnow.py` encerra cedo sem tocar a rede |

Com domínio próprio, deixe `base_path` vazio e preencha `dominio`. Isso também é o que faz o `robots.txt` funcionar: em Pages de projeto ele é servido sob `/<repo>/robots.txt`, e crawler só lê na raiz da origem — que pertence à GitHub, não a você.

## Fonte

<https://portalunico.siscomex.gov.br/cadatributos/api/atributo-ncm/download/json?perfil=PUBLICO> — Receita Federal, público, sem autenticação.

Projeto independente, sem vínculo com a Receita Federal ou com o Portal Único.

## Contribuir e segurança

Regras de código, testes e branches em [CONTRIBUTING.md](CONTRIBUTING.md). Para relatar um problema de segurança, [SECURITY.md](SECURITY.md).

## Licença

Código sob licença MIT — ver [LICENSE](LICENSE). Os dados vêm de fonte pública do governo federal e não são objeto de direito autoral deste projeto; use à vontade, sem exigência de crédito — ver [LICENSE-DADOS.md](LICENSE-DADOS.md).
