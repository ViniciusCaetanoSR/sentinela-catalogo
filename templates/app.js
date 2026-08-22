/* Todo o JS do site, servido como arquivo externo com hash no nome.
   Inline, eram 4,2 KB repetidos em cada uma das paginas. */

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
