# Sentinela do Catálogo

Monitora a relação pública de **atributos por NCM** do Catálogo de Produtos do Portal Único (Siscomex) e publica, todo dia, quais atributos hoje opcionais têm data marcada para virar obrigatórios — e portanto quais produtos serão desativados se não forem preenchidos até lá.

**Isto não é um produto. É um teste de demanda de 60 dias**, com critério de morte escrito antes de começar. Ver o hub no Notion.

## A regra

```
obrigatorio == false  AND  dataFimVigencia > hoje (America/Sao_Paulo)
```

Em 20/08/2026 isso devolve 14 vínculos em 9 NCMs — todos do INMETRO, treze com corte em 30/08 e um em 13/09.

## Como roda

```
coletor.py     baixa o ZIP oficial, extrai as viradas -> dados/ultimo.json + dados/historico/AAAA-MM-DD.json
gerar_site.py  lê dados/ultimo.json -> site/ (HTML estático, sem rede)
```

GitHub Actions roda os dois todo dia às 09:00 UTC, commita o resultado e publica em Pages. Sem servidor, sem banco, sem dependências: **só a biblioteca padrão do Python**.

```bash
python coletor.py      # baixa e apura
python gerar_site.py   # gera site/
```

## Teste de aceitação

`coletor.py` imprime as contagens de controle contra o que foi medido no reconhecimento de 20/08/2026:

| | referência |
|---|---|
| versão do arquivo | `"345"` (string, não int) |
| NCMs | 10.571 |
| atributos distintos | 1.311 |
| vínculos NCM–atributo | 73.246 |
| obrigatórios | 15.078 |
| com `dataFimVigencia` | 14 |
| `dataInicioVigencia` futura | 0 |

Divergência logo na primeira execução é leitura errada da estrutura, não dado errado. Com o tempo os números mudam legitimamente — o arquivo é vivo.

## Armadilhas do endpoint

Todas confirmadas contra o servidor real. Estão tratadas no código; não as remova achando que são paranoia.

1. **`?perfil=PUBLICO` é obrigatório.** Sem ele vem `307` e, se o redirect não for seguido, 304 bytes de HTML no lugar do ZIP.
2. **`Accept: application/json` devolve `406`.** O endpoint só serve `application/zip`. É o header que qualquer um poria num coletor de "JSON".
3. **`dataFimVigencia` tem duas convenções de ausência no mesmo arquivo:** em `listaAtributos` a chave é omitida; em `detalhesAtributos` vem como `""`. Comparar string vazia como data inverte o filtro.
4. **Os bytes do ZIP mudam a cada requisição** (o mtime interno é o instante da geração). Para detectar mudança, hasheie o JSON descompactado ou compare `versao` — nunca o ZIP.
5. **O nome do arquivo interno muda todo dia** (`ATRIBUTOS_POR_NCM_AAAA_MM_DD.json`). Use `namelist()[0]`.
6. **`?data=AAAA_MM_DD` é silenciosamente ignorado.** Não existe histórico oficial — inclusive um valor inválido devolve o arquivo de hoje sem erro. Por isso `dados/historico/` existe.
7. 493 KB comprimidos viram 16,5 MB; pico de ~200 MB no `json.loads`.
8. Sem `Content-Length` (chunked). Sem rate limit observado — ainda assim, colete 1×/dia.

## Por que guardamos o histórico

Não é fosso — o campo `dataFimVigencia` é aviso público, qualquer um lê. É **suprimento de conteúdo**: as viradas chegam em lotes e a lista esvazia entre eles (em 31/08/2026 ela cai de 14 para 1). Sem o arquivo local não existe "o que mudou nos últimos 30 dias", e a página fica vazia. Custo marginal zero: um commit por dia.

## Configuração

`config.json`:

```json
{ "base_url": "https://seudominio.com.br", "form_embed_url": "https://tally.so/embed/..." }
```

Ambos vazios por padrão — o site gera assim mesmo, com o bloco de captura como placeholder visível e `canonical`/`sitemap` em caminho relativo.

## Fonte

<https://portalunico.siscomex.gov.br/cadatributos/api/atributo-ncm/download/json?perfil=PUBLICO> — Receita Federal, público, sem autenticação.

Projeto independente, sem vínculo com a Receita Federal ou com o Portal Único.
