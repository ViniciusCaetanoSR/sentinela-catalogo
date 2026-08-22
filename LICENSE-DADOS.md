# Licença dos dados

A licença MIT em [LICENSE](LICENSE) cobre o **código** deste repositório:
`coletor.py`, `gerar_site.py`, `indexnow.py`, `servir.py`, os templates, os
testes e os workflows.

Os **dados** são outra coisa. Tudo o que está em `dados/` e tudo o que o
site exibe vem da relação pública de atributos por NCM do Portal Único de
Comércio Exterior (Receita Federal):

<https://portalunico.siscomex.gov.br/cadatributos/api/atributo-ncm/download/json?perfil=PUBLICO>

São dados públicos do governo federal. Não são objeto de direito autoral
deste projeto e podem ser usados, copiados e redistribuídos sem exigência de
crédito. O que o projeto acrescenta - snapshots diários em `dados/historico/`,
a apuração de "o que vira obrigatório" e o histórico de mudanças - é derivado
mecanicamente da fonte e segue o mesmo regime: use à vontade.

Este projeto é independente, sem vínculo com a Receita Federal ou com o
Portal Único. Em caso de divergência, vale o arquivo oficial.

Por que esta nota fica num arquivo separado: o GitHub detecta a licença pelo
conteúdo de `LICENSE`, e qualquer parágrafo a mais depois do texto da MIT
faz a detecção falhar.
