# Sentinela do Catálogo

Monitora a relação pública de **atributos por NCM** do Catálogo de Produtos do Portal Único (Siscomex) e publica, todo dia, quais atributos hoje opcionais têm data marcada para virar obrigatórios — e portanto quais produtos serão desativados se não forem preenchidos até lá.

**Isto não é um produto. É um teste de demanda de 60 dias**, com critério de morte escrito antes de começar. Ver o hub no Notion.

## A regra

```
obrigatorio == false  AND  dataFimVigencia >= hoje (America/Sao_Paulo)
```

O corte é `>=`, não `>`: no dia exato da virada o vínculo tem de continuar aparecendo. É o dia mais acionável para quem mantém o catálogo.

## Como roda

```
coletor.py     baixa o ZIP oficial e apura
               -> dados/ultimo.json          snapshot do dia
               -> dados/historico/AAAA-MM-DD.json
               -> dados/atributos.json       os atributos que ganham página
               -> dados/completo.json        mapa NCM->atributos (NÃO versionado)
gerar_site.py  lê os três -> site/ (HTML estático, sem rede)
```

GitHub Actions roda os dois todo dia às 09:00 UTC, commita `dados/` e publica `site/` em Pages. Sem servidor, sem banco, sem dependências: **só a biblioteca padrão do Python**.

```bash
python coletor.py                      # baixa e apura
python gerar_site.py                   # gera site/
python -m unittest discover -s tests   # testes, sem rede
```

Roda em **Python 3.9+** (o piso é o `zoneinfo`). O CI fixa 3.12. O gerador tinha duas f-strings com aspas aninhadas que exigiam 3.12 sem que nada dissesse isso — em 3.11 o script morria com um `SyntaxError` seco; foram reescritas.

## O que o site publica

| páginas | o quê |
|---|---|
| ~10.600 | uma por NCM com atributo exigido, com a tabela completa e as opções válidas |
| ~100 | índice por capítulo da nomenclatura (o caminho de rastreio até as NCMs) |
| ~390 | uma por atributo que tem conteúdo próprio para dizer |
| ~17 | uma por órgão anuente |

Um atributo só ganha página própria se tiver algo próprio a dizer. Os que valem para **uma única NCM** e cuja prosa é boilerplate repetido — 586 atributos chamados "Destaque", com a mesma orientação de 31 caracteres — não ganham: o conteúdo deles aparece dentro da página da NCM, que é onde ele sempre pertenceu. Sem esse corte, dois terços do site eram quase-duplicatas e as 888 páginas de atributo produziam 118 títulos distintos.

## Portão de sanidade

`coletor.py` **recusa gravar** se a colheita vier degenerada: `versao` ausente, `detalhesAtributos` vazio, contagens abaixo de um piso absoluto, ou queda de mais de 10% em relação ao snapshot anterior.

Isso não é paranoia. Sem o portão, um ZIP válido com `listaNcm` vazia derrubava `dados/atributos.json` de 1 MB para 85 bytes, o site de 918 páginas para 5, o sitemap avisava o Google disso, e o workflow commitava e publicava tudo **com exit 0**. O endpoint ignora `?data=`: o dia não volta.

As invariantes de *forma* (`versao` é string, `detalhes == distintos`, nenhum registro descartado) são impressas a cada execução e testadas em `tests/`. As de *magnitude* ficam no portão, com base rolante — uma tabela de números congelados vira ruído em dois dias.

## Armadilhas do endpoint

Todas confirmadas contra o servidor real. Estão tratadas no código; não as remova achando que são paranoia.

1. **`?perfil=PUBLICO` é obrigatório.** Sem ele vem `307` e, se o redirect não for seguido, 304 bytes de HTML no lugar do ZIP.
2. **`Accept: application/json` devolve `406`.** O endpoint só serve `application/zip`. É o header que qualquer um poria num coletor de "JSON". O coletor manda `Accept: */*` e não repete tentativa em 4xx.
3. **`dataFimVigencia` tem duas convenções de ausência no mesmo arquivo:** em `listaAtributos` a chave é omitida; em `detalhesAtributos` vem como `""`. Comparar string vazia como data inverte o filtro.
4. **Os bytes do ZIP mudam a cada requisição** (o mtime interno é o instante da geração). Para detectar mudança, hasheie o JSON descompactado ou compare `versao` — nunca o ZIP.
5. **O nome do arquivo interno muda todo dia** (`ATRIBUTOS_POR_NCM_AAAA_MM_DD.json`). Use `namelist()[0]`.
6. **`?data=AAAA_MM_DD` é silenciosamente ignorado.** Não existe histórico oficial — inclusive um valor inválido devolve o arquivo de hoje sem erro. Por isso `dados/historico/` existe.
7. 493 KB comprimidos viram 16,5 MB; pico de ~200 MB no `json.loads`.
8. Sem `Content-Length` (chunked). Sem rate limit observado — ainda assim, colete 1×/dia.
9. **Página de manutenção servida com `200`** vira `BadZipFile` sem explicação. O coletor confere o `Content-Type` antes de tentar descompactar.

## Por que guardamos o histórico

Não é fosso — o campo `dataFimVigencia` é aviso público, qualquer um lê. É **suprimento de conteúdo**: as viradas chegam em lotes e a lista esvazia entre eles. Sem o arquivo local não existe "o que mudou nos últimos 30 dias", e a página fica vazia. Custo marginal zero.

A janela é de 30 **dias**, não dos 30 últimos arquivos: um dia perdido por falha de rede alargaria o período em silêncio.

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
| `indexnow_key` | `""` | sem arquivo de chave, e o passo de ping do workflow não roda |

Com domínio próprio, deixe `base_path` vazio e preencha `dominio`. Isso também é o que faz o `robots.txt` funcionar: em Pages de projeto ele é servido sob `/<repo>/robots.txt`, e crawler só lê na raiz da origem — que pertence à GitHub, não a você.

## Fonte

<https://portalunico.siscomex.gov.br/cadatributos/api/atributo-ncm/download/json?perfil=PUBLICO> — Receita Federal, público, sem autenticação.

Projeto independente, sem vínculo com a Receita Federal ou com o Portal Único.

## Licença

Código sob licença MIT — ver [LICENSE](LICENSE). Os dados vêm de fonte pública do governo federal e não são objeto de direito autoral deste projeto; use à vontade, sem exigência de crédito.
