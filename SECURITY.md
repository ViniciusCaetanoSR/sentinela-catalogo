# Segurança

## Como relatar

Escreva para **orangestripes.ai@gmail.com** com o assunto "Sentinela do Catálogo: segurança". Não abra issue pública para vulnerabilidade: o repositório é público e a issue seria lida antes da correção.

Prazo de resposta: **7 dias** para confirmar o recebimento e dizer se o problema procede. Correção e crédito (se você quiser) combinados a partir daí. Projeto de uma pessoa, sem bounty.

## Escopo

O que existe é pequeno:

- **O site é estático.** HTML, CSS, um JavaScript sem rede e fontes, servidos pelo GitHub Pages. Não há servidor próprio, banco, login, cookie nem formulário que receba dado (a captura é um `mailto:` ou um iframe de terceiro declarado em `config.json`).
- **O coletor roda no GitHub Actions**, uma vez por dia, com o token padrão do repositório e permissões mínimas por job (`contents: write` só onde commita; `issues: write` só onde avisa). As actions são presas por SHA.
- **Não há segredo no repositório.** `config.json` é público por desenho; a chave do IndexNow, se existir, é publicada de propósito (é assim que o protocolo prova posse do domínio).

O vetor realista é **o conteúdo que vem do arquivo oficial da Receita**: nomes de atributos, descrições, orientações de preenchimento e nomes de órgãos entram nas páginas. Todo dado oficial passa por `esc()` antes de ir para o HTML e o feed; o teste de integridade confere que nada cru chega ao site. Se você encontrar um ponto em que um valor do arquivo oficial é interpolado sem escape, ou um jeito de um dado do arquivo quebrar a página, isso é exatamente o que queremos saber.

Fora de escopo: o próprio Portal Único, o GitHub Pages e o GoatCounter — relate a quem os mantém.

## Versões

Só a ponta de `main` recebe correção. Não há versões antigas suportadas.
