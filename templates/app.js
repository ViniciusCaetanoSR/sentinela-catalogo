/* Todo o JS do site, servido como arquivo externo com hash no nome.
   Inline, eram 4,2 KB repetidos em cada uma das paginas. */

/* Prazos recalculados no navegador, e a faixa de dado velho.

   O build roda de manhã, com o "hoje" de Brasília. Quem abre a página à
   noite, ou dias depois - porque a coleta parou e o site continua no ar
   com cara de saudável -, leria "faltam 3 dias" quando faltam 2, ou
   nenhum. Cada elemento que mostra um prazo carrega data-corte (a data da
   virada); aqui o prazo é refeito com a data LOCAL do navegador, sem hora,
   e o texto é reescrito com as MESMAS regras do gerar_site.py. Sem JS, o
   texto do build fica: ele já está no HTML.

   TEXTOS_PRAZO é uma cópia literal de gerar_site.TEXTOS_PRAZO, em JSON
   estrito (chaves entre aspas duplas, sem comentário dentro): um teste lê
   este bloco com json.loads e compara com o do Python, para o texto nunca
   divergir entre o que o build escreve e o que este script reescreve.
   Os placeholders são {n} (dias, sempre positivo), {dia} ("dia"/"dias")
   e {data} (DD/MM/AAAA). Roda ANTES da contagem animada, que lê o valor
   recalculado de data-contagem. */
(function(){
  var TEXTOS_PRAZO = {
      "curto": {
          "vencido": "prazo vencido há {n} {dia}",
          "hoje": "hoje",
          "amanha": "amanhã",
          "futuro": "em {n} dias"
      },
      "frase": {
          "vencido": "Prazo vencido há {n} {dia}.",
          "hoje": "É hoje.",
          "amanha": "Falta 1 dia.",
          "futuro": "Faltam {n} dias."
      },
      "contagem": {
          "vencido": "O próximo corte foi em {data}, há {n} {dia}.",
          "hoje": "O próximo corte é hoje, {data}.",
          "amanha": "Falta 1 dia para o próximo corte, em {data}.",
          "futuro": "Faltam {n} dias para o próximo corte, em {data}."
      },
      "unidade": {
          "vencido": "{dia} atrás",
          "hoje": "dias",
          "amanha": "dia",
          "futuro": "dias"
      },
      "h1": {
          "vencido": "há {n} {dia}",
          "hoje": "hoje",
          "amanha": "amanhã",
          "futuro": "nos próximos {n} dias"
      }
  };
  /* os mesmos de gerar_site.py: DIAS_BARRA_CHEIA e DIAS_URGENTE */
  var DIAS_BARRA_CHEIA = 30, DIAS_URGENTE = 7;
  /* acima disto a coleta diária deve ter parado (o vigia.yml usa o mesmo
     "mais de dois dias"); a folga cobre o fuso de quem lê de fora */
  var DIAS_DADO_VELHO = 2;
  var RE_DATA = /^(\d{4})-(\d{2})-(\d{2})$/;

  function plural(n){ return n === 1 ? "dia" : "dias"; }
  function casoPrazo(dias){
    return dias < 0 ? "vencido" : dias === 0 ? "hoje" : dias === 1 ? "amanha" : "futuro";
  }
  function prazoHumano(dias, estilo, data){
    var n = Math.abs(dias);
    return TEXTOS_PRAZO[estilo][casoPrazo(dias)]
      .replace("{n}", String(n))
      .replace("{dia}", plural(n))
      .replace("{data}", data || "");
  }
  function larguraPrazo(dias){
    return Math.min(100, Math.max(6, Math.round(dias / DIAS_BARRA_CHEIA * 100)));
  }
  function br(iso){
    var m = RE_DATA.exec(iso || "");
    return m ? m[3] + "/" + m[2] + "/" + m[1] : iso;
  }

  /* Data local sem hora, em UTC para a conta não atravessar horário de
     verão de quem lê de fora do Brasil. */
  var agora = new Date();
  var hoje = Date.UTC(agora.getFullYear(), agora.getMonth(), agora.getDate());
  function diasAte(iso){
    var m = RE_DATA.exec(iso || "");
    if(!m) return null;
    var alvo = Date.UTC(+m[1], +m[2] - 1, +m[3]);
    if(isNaN(alvo)) return null;
    return Math.round((alvo - hoje) / 86400000);
  }
  function urgencia(el, dias){
    if(dias <= DIAS_URGENTE) el.classList.add("urgente");
    else el.classList.remove("urgente");
  }

  try{
    /* células da tabela: texto, urgência e a barra irmã */
    [].slice.call(document.querySelectorAll(".prazo-txt[data-corte]")).forEach(function(el){
      var dias = diasAte(el.getAttribute("data-corte"));
      if(dias === null) return;
      el.textContent = prazoHumano(dias, "curto");
      urgencia(el, dias);
      var barra = el.nextElementSibling;
      if(barra && barra.classList.contains("prazo")){
        urgencia(barra, dias);
        var i = barra.querySelector("i");
        if(i) i.style.setProperty("--w", larguraPrazo(dias) + "%");
      }
    });

    /* o <strong> dos avisos de NCM e de atributo */
    [].slice.call(document.querySelectorAll("strong[data-corte]")).forEach(function(el){
      var dias = diasAte(el.getAttribute("data-corte"));
      if(dias === null) return;
      el.textContent = prazoHumano(dias, "frase");
    });

    /* o que declara o próprio molde (o fim do h1 da home) */
    [].slice.call(document.querySelectorAll("[data-corte][data-estilo]")).forEach(function(el){
      var dias = diasAte(el.getAttribute("data-corte"));
      var estilo = el.getAttribute("data-estilo");
      if(dias === null || !TEXTOS_PRAZO[estilo]) return;
      el.textContent = prazoHumano(dias, estilo);
    });

    /* o cartão da home: número, unidade, frase para leitor de tela - e o
       data-contagem que a animação abaixo lê. O data-contagem do build fica
       como fallback quando data-corte não for uma data. */
    var num = document.querySelector(".contagem-num[data-corte]");
    if(num){
      var corte = num.getAttribute("data-corte");
      var diasCorte = diasAte(corte);
      if(diasCorte !== null){
        var n = Math.abs(diasCorte);
        num.textContent = String(n);
        num.style.setProperty("--digitos", String(String(n).length));
        num.setAttribute("data-contagem", String(diasCorte));
        var topo = num.parentNode;
        var un = topo && topo.querySelector(".contagem-un");
        if(un) un.textContent = prazoHumano(diasCorte, "unidade");
        var oculto = topo && topo.querySelector(".oculto");
        if(oculto) oculto.textContent = prazoHumano(diasCorte, "contagem", br(corte));
      }
    }
  }catch(e){}

  /* Faixa de dado velho: a idade do snapshot vem de data-referencia no
     <body>. O dead-man switch do GitHub avisa quem mantém; esta faixa
     avisa quem lê. */
  try{
    var ref = document.body && document.body.getAttribute("data-referencia");
    var diasRef = diasAte(ref);
    if(diasRef !== null && -diasRef > DIAS_DADO_VELHO){
      var idade = -diasRef;
      var main = document.querySelector("main");
      if(main){
        var faixa = document.createElement("div");
        faixa.className = "dado-velho";
        faixa.setAttribute("role", "status");
        faixa.textContent =
          "Estes dados são de " + br(ref) + " (há " + idade + " " + plural(idade) + "). " +
          "A coleta diária pode ter parado — confira no sistema oficial.";
        main.insertBefore(faixa, main.firstChild);
      }
    }
  }catch(e){}
})();

/* Contagem do numero grande.

   Roda SEMPRE, inclusive com prefers-reduced-motion. Desvio deliberado do
   patch original: um digito trocando no lugar nao e o tipo de movimento que
   a preferencia visa (parallax, zoom, deslocamento grande). Todo o resto do
   site continua respeitando a preferencia pelo @media no CSS.

   Comeca IMEDIATAMENTE. A versao anterior esperava 200 ms antes de zerar:
   o leitor via o numero certo, ele virava 0, e so depois subia de volta -
   ~1,3 s exibindo um valor que nao e a resposta, na unica pergunta que a
   pagina existe para responder. A largura final ja esta reservada no CSS
   por --digitos, entao contar nao desloca o "dias" ao lado.

   O valor vem de data-contagem, que o bloco acima já trocou pelo prazo
   recalculado com o relógio do navegador (o do build fica quando
   data-corte não é uma data). Prazo vencido ou de hoje não anima.

   Escrito defensivamente porque nao da para depurar no navegador do usuario:
   nao depende de requestAnimationFrame, nao depende do DOM estar pronto, e
   qualquer excecao cai no valor final em vez de deixar a pagina quebrada. */
(function(){
  function iniciar(){
    var alvo = document.querySelector("[data-contagem]");
    if(!alvo) return;
    try{
      var fim = parseInt(alvo.getAttribute("data-contagem"), 10);
      if(isNaN(fim) || fim <= 0) return;

      var dur = Math.min(1800, Math.max(700, fim * 120));
      var passos = Math.max(2, Math.min(fim, 30));
      var intervalo = Math.round(dur / passos);
      var i = 0;

      alvo.textContent = "0";
      var timer = setInterval(function(){
        i++;
        var p = i / passos;
        if(p >= 1){
          clearInterval(timer);
          alvo.textContent = String(fim);
          return;
        }
        alvo.textContent = String(Math.round(fim * (1 - Math.pow(1 - p, 2))));
      }, intervalo);

      /* rede de seguranca: se algo travar o intervalo, o numero nao fica errado */
      setTimeout(function(){
        clearInterval(timer);
        alvo.textContent = String(fim);
      }, dur + 800);
    }catch(e){
      alvo.textContent = alvo.getAttribute("data-contagem");
    }
  }

  if(document.readyState === "loading"){
    document.addEventListener("DOMContentLoaded", iniciar);
  }else{
    iniciar();
  }
})();

/* Revelacao ao entrar na tela.

   Substitui a cascata de load nas tabelas: a pagina do SECEX tem centenas de
   linhas, e anima-las todas no primeiro segundo gastava o efeito onde
   ninguem via. Agora as linhas acendem conforme o leitor chega nelas.

   Tambem revela o bloco de captura, que fica no fim de paginas longas e ate
   agora aparecia sem tratamento nenhum - e e o ponto que o teste mede.

   Degrada bem: sem IntersectionObserver, tudo e revelado de uma vez. */
(function(){
  var alvos = [].slice.call(document.querySelectorAll("tbody tr, .captura, ul.limpa li"));
  if(!alvos.length) return;

  function revelarTudo(){
    alvos.forEach(function(el){ el.classList.add("revelar", "revelado"); });
  }
  if(!("IntersectionObserver" in window)){ revelarTudo(); return; }

  alvos.forEach(function(el){ el.classList.add("revelar"); });

  var obs = new IntersectionObserver(function(entradas){
    var vistos = 0;
    entradas.forEach(function(e){
      if(!e.isIntersecting) return;
      /* escalona so dentro do lote visivel, ate 8 - assim a cascata existe
         na primeira tela e nao vira espera nas seguintes */
      var atraso = Math.min(vistos, 8) * 45;
      vistos++;
      var el = e.target;
      setTimeout(function(){ el.classList.add("revelado"); }, atraso);
      obs.unobserve(el);
    });
  }, {rootMargin: "0px 0px -40px 0px", threshold: 0.01});

  alvos.forEach(function(el){ obs.observe(el); });

  /* rede de seguranca: nada pode ficar invisivel por falha do observer */
  setTimeout(revelarTudo, 3000);
})();

/* Copiar codigo. Sem clipboard API o botao nem aparece (CSS esconde quando
   o JS nao roda; aqui removemos se a API faltar).

   O retorno vai para um <span role="status"> irmao, nao para o rotulo do
   proprio botao: trocar o nome acessivel de um elemento com foco e
   anunciado de forma inconsistente entre NVDA, JAWS e VoiceOver, e o
   rotulo voltava ao normal em 1,4 s - as vezes antes de ser lido. */
(function(){
  var botoes = [].slice.call(document.querySelectorAll("[data-copiar]"));
  if(!botoes.length) return;
  if(!(navigator.clipboard && navigator.clipboard.writeText)){
    botoes.forEach(function(b){ b.style.display = "none"; });
    return;
  }
  botoes.forEach(function(b){
    var status = document.getElementById(b.getAttribute("aria-describedby") || "");
    b.addEventListener("click", function(){
      var valor = b.getAttribute("data-copiar");
      navigator.clipboard.writeText(valor).then(function(){
        b.setAttribute("data-feito", "1");
        if(status) status.textContent = valor + " copiado";
        setTimeout(function(){
          b.removeAttribute("data-feito");
          if(status) status.textContent = "";
        }, 2500);
      }).catch(function(){
        /* A API existe mas a escrita falhou: permissao negada, pagina sem
           foco, iframe sem clipboard-write. Sem este catch a promessa
           rejeitada morria em silencio e o botao parecia ter funcionado. */
        b.setAttribute("data-erro", "1");
        if(status) status.textContent =
          "Não foi possível copiar — selecione o código e use Ctrl+C";
        setTimeout(function(){
          b.removeAttribute("data-erro");
          if(status) status.textContent = "";
        }, 5000);
      });
    });
  });
})();

/* "Ir para a NCM": um form sem backend.

   O que a pessoa digita vira só dígitos: 84151090, 8415.10.90 e
   8415 10 90 são a mesma NCM. Oito dígitos vão para a página da NCM na
   forma pontuada (é a única URL que existe); de 4 a 7, para o capítulo
   (os dois primeiros); o resto recebe uma mensagem inline, sem sair da
   página. Sem JS o GET cai em /ncm/?ncm=..., o índice por capítulo - e se
   o script chegar depois (ou numa URL compartilhada com ?ncm=), o mesmo
   caminho resolve.

   Na página de erro (form com data-404) o script também lê a URL que deu
   404: /ncm/<8 dígitos>/ redireciona para a forma pontuada; uma NCM
   pontuada que não tem página deixa o campo preenchido para corrigir. */
(function(){
  var forms = [].slice.call(document.querySelectorAll("form.ir-ncm"));
  if(!forms.length) return;

  function destino(base, bruto){
    var d = (bruto || "").replace(/\D/g, "");
    if(d.length === 8){
      return base + "/ncm/" + d.slice(0, 4) + "." + d.slice(4, 6) + "." + d.slice(6) + "/";
    }
    if(d.length >= 4 && d.length <= 7) return base + "/ncm/capitulo-" + d.slice(0, 2) + "/";
    return null;
  }

  forms.forEach(function(form){
    /* o prefixo do base_path, tirado do próprio action ("/repo/ncm/") */
    var base = (form.getAttribute("action") || "").replace(/\/ncm\/?$/, "");
    var campo = form.querySelector("input[name=ncm]");
    var erro = form.querySelector(".ir-ncm-erro");
    if(!campo) return;

    function avisar(texto){ if(erro) erro.textContent = texto; }
    /* substituir: a URL com ?ncm= é só escala; se ficasse no histórico, o
       botão Voltar cairia nela, o script navegaria de novo e a pessoa
       nunca sairia da página da NCM. */
    function ir(valor, substituir){
      var alvo = destino(base, valor);
      if(alvo){ if(substituir) location.replace(alvo); else location.assign(alvo); return; }
      avisar("Digite os 8 dígitos da NCM (como 8415.10.90) ou só os 4 primeiros, " +
        "para ir ao capítulo.");
    }

    form.addEventListener("submit", function(ev){
      ev.preventDefault();
      ir(campo.value);
    });
    campo.addEventListener("input", function(){ avisar(""); });

    var consulta = /[?&]ncm=([^&]*)/.exec(location.search);
    if(consulta && !campo.value){
      var valor = decodeURIComponent(consulta[1].replace(/\+/g, " "));
      campo.value = valor;
      ir(valor, true);
      return;
    }

    if(form.hasAttribute("data-404")){
      var caminho = location.pathname;
      var semPontos = /\/ncm\/(\d{4})(\d{2})(\d{2})\/?$/.exec(caminho);
      if(semPontos){
        location.replace(caminho.replace(/\/ncm\/\d{8}\/?$/,
          "/ncm/" + semPontos[1] + "." + semPontos[2] + "." + semPontos[3] + "/"));
        return;
      }
      var pontuada = /\/ncm\/(\d{4}\.\d{2}\.\d{2})\/?$/.exec(caminho);
      if(pontuada){
        campo.value = pontuada[1];
        avisar("A NCM " + pontuada[1] + " não tem página aqui. Confira o código, ou " +
          "vá ao capítulo " + pontuada[1].slice(0, 2) + " para ver as NCMs que existem.");
      }
    }
  });
})();
