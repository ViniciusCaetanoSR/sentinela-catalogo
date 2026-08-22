"""
Coletor do Catálogo de Produtos do Portal Único (Siscomex).

Baixa a relação pública de atributos por NCM, extrai as viradas agendadas
- atributos hoje opcionais com data marcada para virar obrigatórios - e
grava um snapshot diário enxuto.

Regra do produto:
    obrigatorio == false AND dataFimVigencia >= hoje (America/Sao_Paulo)

Sem dependências externas. Somente biblioteca padrão.
"""

__version__ = "0.1.0"

import collections
import email.utils
import hashlib
import http.client
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# O ?perfil=PUBLICO é obrigatório: sem ele o servidor devolve 307 e, se o
# redirect não for seguido, 304 bytes de HTML no lugar do ZIP.
URL = (
    "https://portalunico.siscomex.gov.br/cadatributos/api"
    "/atributo-ncm/download/json?perfil=PUBLICO"
)

# Identifica o coletor para quem olhar o log do servidor. Sem isso vai
# "Python-urllib/3.x", que é o primeiro padrão que um WAF corta.
AGENTE = (
    f"SentinelaDoCatalogo/{__version__} "
    "(+https://github.com/viniciuscaetanosr/sentinela-catalogo)"
)

RAIZ = os.path.dirname(os.path.abspath(__file__))
DIR_DADOS = os.path.join(RAIZ, "dados")

# Os arquivos que o coletor lê e grava, agrupados para poderem apontar para
# um diretório qualquer: os testes de ponta a ponta gravam num diretório
# temporário, e sem isto precisariam remendar seis constantes do módulo.
Caminhos = collections.namedtuple(
    "Caminhos", "dados historico ultimo atributos completo bruto"
)


def caminhos_em(dir_dados):
    """Os caminhos do coletor dentro de um diretório de dados."""
    return Caminhos(
        dados=dir_dados,
        historico=os.path.join(dir_dados, "historico"),
        ultimo=os.path.join(dir_dados, "ultimo.json"),
        atributos=os.path.join(dir_dados, "atributos.json"),
        # Mapa completo NCM -> atributos. NÃO é versionado (ver .gitignore):
        # são ~4,8 MB (~120 KB comprimidos) que mudam todo dia e o gerador
        # consome no mesmo run do CI; o render.yml os recebe via cache.
        completo=os.path.join(dir_dados, "completo.json"),
        # O ZIP exatamente como veio da Receita. Também fora do git (*.zip):
        # o workflow guarda como artifact e, quando o conteúdo muda, como
        # asset de Release - é o que permite reapurar um dia sem bater no
        # endpoint, que não serve versões passadas.
        bruto=os.path.join(dir_dados, "bruto.zip"),
    )


CAMINHOS = caminhos_em(DIR_DADOS)
DIR_HISTORICO = CAMINHOS.historico
ARQ_ULTIMO = CAMINHOS.ultimo
ARQ_ATRIBUTOS = CAMINHOS.atributos
ARQ_COMPLETO = CAMINHOS.completo
ARQ_BRUTO = CAMINHOS.bruto

# Tetos de tamanho. O ZIP real tem ~500 KB e o JSON ~17 MB: folga para anos
# de crescimento, mas um servidor confuso não enche a memória do runner.
MAX_ZIP = 20 * 1024 * 1024
MAX_JSON = 200 * 1024 * 1024
# Os quatro primeiros bytes de todo arquivo ZIP com ao menos uma entrada.
ASSINATURA_ZIP = b"PK\x03\x04"
# Quanto do corpo de uma resposta inesperada entra na mensagem de erro.
AMOSTRA_CORPO = 200

# Falhas que valem nova tentativa: rede, corpo lido pela metade
# (IncompleteRead e ConnectionResetError NÃO são URLError - escapavam do
# retry e custavam o dia com um traceback), ZIP truncado e as checagens de
# conteúdo do próprio coletor (página de manutenção servida com 200).
TRANSITORIAS = (
    urllib.error.URLError,
    OSError,
    http.client.HTTPException,
    zipfile.BadZipFile,
    RuntimeError,
)
# 4xx com que o servidor diz "agora não", e não "você errou".
REPETIVEIS_4XX = (408, 429)
# Esperas entre tentativas, em segundos: ~5 min no total, cabe nos 15 min
# do job. Uma falha transitória às 09:00 UTC custa o dia inteiro, e o
# endpoint ignora ?data= - não existe como buscar o arquivo de ontem.
ESPERAS = (10, 30, 90, 180)
# Um Retry-After maior que isto estoura o job; melhor tentar e falhar.
TETO_RETRY_AFTER = 300

# Versão do formato do snapshot. O gerador lê até 30 arquivos de
# dados/historico/ escritos por versões antigas do código e IGNORA os que
# declararem schema maior do que ele suporta - por isso toda mudança de
# forma incompatível tem de incrementar este número.
SCHEMA = 1

# Campos que mudam a cada execução sem que o dado tenha mudado. Ficam fora
# da comparação que decide se vale reescrever o arquivo - senão todo run
# produz um commit que não carrega informação nenhuma. Os quatro últimos são
# fatos sobre a execução ("o ZIP de hoje é outro", "o catálogo foi reescrito
# agora"), não sobre o catálogo - numa segunda rodada do mesmo dia eles
# mudam sem que nada tenha mudado.
VOLATEIS = (
    "coletado_em",
    "bytes_zip",
    "disposition",
    "catalogo_reescrito",
    "bruto_novo",
    "conteudo_identico",
    "portao_ignorado",
)

# Quantas datas inválidas entram no snapshot e no log. Ver datas_invalidas().
MAX_DATAS_LOGADAS = 20
# Quantos problemas de forma cabem numa mensagem de erro legível.
MAX_PROBLEMAS_LISTADOS = 10


def avisar(mensagem):
    """Aviso que não bloqueia a coleta.

    O prefixo é o formato de comando do GitHub Actions: a linha vira uma
    anotação amarela no resumo do job, em vez de se perder no meio do log.
    """
    print(f"::warning::{mensagem}", file=sys.stderr)


def _fuso():
    """America/Sao_Paulo, ou UTC-3 fixo quando a máquina não tem o tzdata.

    Acontece no Windows sem o pacote tzdata. O Brasil não tem horário de
    verão desde 2019, então o deslocamento fixo dá a mesma data.
    """
    try:
        return ZoneInfo("America/Sao_Paulo")
    except ZoneInfoNotFoundError:
        avisar("sem tzdata para America/Sao_Paulo; usando UTC-3 fixo")
        return timezone(timedelta(hours=-3))


FUSO = _fuso()


def _sem_volateis(texto):
    """Ignora as linhas voláteis ao comparar - só o conteúdo importa."""
    nl = chr(10)
    return nl.join(
        linha
        for linha in texto.split(nl)
        if not any('"' + c + '"' in linha for c in VOLATEIS)
    )


def hoje_br():
    """A data de referência da regra, no fuso de Brasília.

    O arquivo é regenerado pelo servidor por volta de 00:0x horário de
    Brasília. Usar UTC deslocaria a janela perto da meia-noite.
    """
    return datetime.now(FUSO).date()


def slug(texto):
    """Nome de órgão -> pedaço de URL. Dobra o acento em vez de descartá-lo.

    Sem a dobra, "Sem órgão declarado" virava "sem-rg-o-declarado": o
    regex [^a-z0-9] come o "ó" e o "ã" inteiros.
    """
    plano = unicodedata.normalize("NFKD", texto or "")
    plano = plano.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", plano.lower()).strip("-") or "sem-nome"


def _gravar_json(caminho, dados):
    """Escrita atômica.

    open(..., "w") trunca antes de escrever: um job cancelado no meio deixa
    o arquivo pela metade, e aí TODA execução seguinte quebra no json.load.
    """
    temporario = caminho + ".tmp"
    with open(temporario, "w", encoding="utf-8", newline="\n") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(temporario, caminho)


def _gravar_bytes(caminho, conteudo):
    """Escrita atômica de bytes, pelo mesmo motivo de _gravar_json."""
    temporario = caminho + ".tmp"
    with open(temporario, "wb") as f:
        f.write(conteudo)
    os.replace(temporario, caminho)


# O que baixar() devolve: o JSON descompactado, os metadados HTTP que vão
# para o snapshot e o ZIP intacto, para ser guardado como veio.
Baixado = collections.namedtuple("Baixado", "conteudo meta bruto")


def _retry_after(erro):
    """Segundos que o servidor pediu num 408/429, com teto. None se não veio.

    Aceita as duas formas do cabeçalho: segundos ou data HTTP.
    """
    cabecalhos = getattr(erro, "headers", None) or {}
    valor = cabecalhos.get("Retry-After")
    if valor is None:
        return None
    valor = str(valor).strip()
    if valor.isdigit():
        segundos = int(valor)
    else:
        try:
            quando = email.utils.parsedate_to_datetime(valor)
        except (TypeError, ValueError):
            return None
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=timezone.utc)
        segundos = (quando - datetime.now(timezone.utc)).total_seconds()
    return max(0, min(int(segundos), TETO_RETRY_AFTER))


def baixar(url=URL, timeout=90, tentativas=5):
    """Devolve Baixado(conteudo_do_json, metadados_http, bytes_do_zip).

    Com retry e espera crescente (ESPERAS). Não repete em 4xx: 406 é header
    errado, e insistir não conserta - exceto 408 e 429, que são o servidor
    pedindo para voltar depois, e aí o Retry-After dele é respeitado.
    """
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        sugerida = None
        try:
            return _baixar_uma_vez(url, timeout)
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code not in REPETIVEIS_4XX:
                raise
            ultimo_erro = e
            sugerida = _retry_after(e)
        except TRANSITORIAS as e:
            ultimo_erro = e
        if tentativa < tentativas:
            espera = ESPERAS[min(tentativa, len(ESPERAS)) - 1]
            if sugerida is not None:
                espera = sugerida
            print(
                f"tentativa {tentativa}/{tentativas} falhou ({ultimo_erro!r}); "
                f"nova tentativa em {espera}s",
                file=sys.stderr,
            )
            time.sleep(espera)
    raise ultimo_erro


def _amostra(corpo):
    """Os primeiros bytes de uma resposta inesperada, numa linha só."""
    texto = corpo[:AMOSTRA_CORPO].decode("utf-8", errors="replace")
    return " ".join(texto.split())


def _baixar_uma_vez(url, timeout):
    # Não enviar Accept: application/json - o endpoint devolve 406.
    # Ele só serve application/zip.
    req = urllib.request.Request(url, headers={"Accept": "*/*", "User-Agent": AGENTE})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        # Um byte além do teto: é o que distingue "coube" de "passou".
        bruto = r.read(MAX_ZIP + 1)
        meta = {
            "status": r.status,
            "content_type": r.headers.get("Content-Type"),
            "bytes_zip": len(bruto),
            "disposition": r.headers.get("Content-Disposition"),
        }
    if len(bruto) > MAX_ZIP:
        raise RuntimeError(f"resposta maior que o teto de {MAX_ZIP} bytes")

    # Página de manutenção servida com 200 é a falha que passa despercebida:
    # sem esta checagem ela vira BadZipFile lá embaixo, sem explicação. Os
    # bytes mágicos valem como prova quando o Content-Type vier errado.
    tipo = (meta["content_type"] or "").lower()
    if "zip" not in tipo and bruto[:4] != ASSINATURA_ZIP:
        raise RuntimeError(
            "esperava application/zip, veio "
            f"{tipo!r} ({len(bruto)} bytes): {_amostra(bruto)}"
        )

    z = zipfile.ZipFile(io.BytesIO(bruto))
    nomes = z.namelist()
    if len(nomes) != 1:
        raise RuntimeError(f"ZIP deveria ter 1 arquivo, tem {len(nomes)}: {nomes}")

    # O nome muda todo dia (ATRIBUTOS_POR_NCM_AAAA_MM_DD.json). Nunca hardcode.
    meta["arquivo_interno"] = nomes[0]
    # O tamanho declarado no cabeçalho do ZIP sai de graça; conferir antes
    # de descompactar evita uma bomba de descompressão.
    tamanho = z.getinfo(nomes[0]).file_size
    if tamanho > MAX_JSON:
        raise RuntimeError(f"JSON declara {tamanho} bytes, acima do teto de {MAX_JSON}")
    conteudo = z.read(nomes[0])
    meta["bytes_json"] = len(conteudo)

    # Os bytes do ZIP mudam a cada requisição porque o mtime interno é o
    # instante da geração. Só o JSON descompactado tem hash estável.
    meta["sha256_json"] = hashlib.sha256(conteudo).hexdigest()
    return Baixado(conteudo, meta, bruto)


RE_DATA_ARQUIVO = re.compile(r"(\d{4})_(\d{2})_(\d{2})")


def data_do_arquivo(nome):
    """A data embutida em ATRIBUTOS_POR_NCM_AAAA_MM_DD.json, ou None."""
    achado = RE_DATA_ARQUIVO.search(nome or "")
    return "-".join(achado.groups()) if achado else None


def carregar(conteudo):
    """JSON -> dict. UTF-8 sem BOM, confirmado."""
    return json.loads(conteudo.decode("utf-8"))


def validar_forma(dados):
    """Recusa o arquivo quando a FORMA não é a esperada. Roda antes do portão.

    O portão vigia magnitude; isto vigia tipo. Um `obrigatorio` que chegasse
    como a string "false" não derrubava nada: `is not False` o tratava como
    obrigatório, viradas() devolvia lista vazia e o site publicava "nada
    agendado" com exit 0. Errado com cara de saudável é o pior modo de falha.
    """
    if not isinstance(dados, dict):
        raise RuntimeError("schema inesperado: a raiz não é um objeto JSON")
    problemas = []
    for chave in ("listaNcm", "detalhesAtributos"):
        if not isinstance(dados.get(chave), list):
            problemas.append(f"{chave} não é lista")
    if not isinstance(dados.get("versao"), str) or not dados["versao"]:
        problemas.append("versao ausente ou não é string")

    lista = dados.get("listaNcm")
    for ncm in lista if isinstance(lista, list) else []:
        if not isinstance(ncm, dict):
            problemas.append(f"item de listaNcm não é objeto: {ncm!r:.60}")
            continue
        codigo = ncm.get("codigoNcm")
        vinculos = ncm.get("listaAtributos")
        if vinculos is None:
            continue
        if not isinstance(vinculos, list):
            problemas.append(f"listaAtributos da NCM {codigo} não é lista")
            continue
        for v in vinculos:
            if not isinstance(v, dict):
                problemas.append(f"vínculo da NCM {codigo} não é objeto: {v!r:.60}")
                continue
            obrigatorio = v.get("obrigatorio")
            if obrigatorio is not None and not isinstance(obrigatorio, bool):
                problemas.append(
                    f"obrigatorio={obrigatorio!r:.40} na NCM {codigo}, atributo "
                    f"{v.get('codigo')} (deveria ser booleano)"
                )

    detalhes = dados.get("detalhesAtributos")
    for d in detalhes if isinstance(detalhes, list) else []:
        if not isinstance(d, dict):
            problemas.append(f"item de detalhesAtributos não é objeto: {d!r:.60}")
            continue
        for chave in ("orgaos", "dominio"):
            valor = d.get(chave)
            if valor is not None and not isinstance(valor, list):
                problemas.append(
                    f"{chave}={valor!r:.40} no atributo "
                    f"{d.get('codigo')} (deveria ser lista)"
                )

    if problemas:
        extra = len(problemas) - MAX_PROBLEMAS_LISTADOS
        resumo = "; ".join(problemas[:MAX_PROBLEMAS_LISTADOS])
        if extra > 0:
            resumo += f" (e mais {extra})"
        raise RuntimeError("schema inesperado: " + resumo)


RE_DATA_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def normalizar_data(valor):
    """'AAAA-MM-DD' estrito -> 'AAAA-MM-DD'. Levanta ValueError se não for.

    Só o formato não basta: "2026-02-30" passa no regex e quebra depois, no
    gerador. E só o fromisoformat não basta: a partir do Python 3.11 ele
    aceita "20260830" e "2026-W35", que o gerador não compara como data.
    """
    if not isinstance(valor, str) or not RE_DATA_ISO.fullmatch(valor):
        raise ValueError(f"data fora do formato AAAA-MM-DD: {valor!r}")
    return date.fromisoformat(valor).isoformat()


def _fim_vigencia(vinculo):
    """Lê dataFimVigencia tolerando as DUAS convenções de ausência.

    Em listaAtributos a chave é omitida quando não há fim.
    Em detalhesAtributos ela vem como string vazia.
    Nunca comparar "" como data: "" < qualquer data é True.

    Valor inválido é tratado como ausente, NÃO derruba a coleta: um único
    "2026-02-30" num arquivo de 73 mil vínculos custava o dia inteiro. Quem
    conta e vigia esses casos é datas_invalidas() e o portão.
    """
    valor = vinculo.get("dataFimVigencia")
    if not valor:
        return None
    try:
        return normalizar_data(valor)
    except ValueError:
        return None


def datas_invalidas(dados):
    """(ncm, atributo, valor) de cada dataFimVigencia que não é data.

    Alimenta contagens()["datas_invalidas"], o portão (aborta acima de
    LIMITE_DATAS_INVALIDAS) e o log, que mostra as primeiras para que o
    problema seja reportado à fonte com código e valor.
    """
    saida = []
    for ncm in lista_ncms(dados):
        for v in vinculos_de(ncm):
            valor = v.get("dataFimVigencia")
            if not valor:
                continue
            try:
                normalizar_data(valor)
            except ValueError:
                saida.append((ncm["codigoNcm"], v["codigo"], valor))
    return saida


def lista_ncms(dados):
    """listaNcm sem duplicata e sem registro capenga.

    NCM repetida gerava duas fichas para o mesmo código - a mesma página
    escrita duas vezes e o total_ncms contado em dobro.
    """
    vistas = {}
    for ncm in dados.get("listaNcm") or []:
        codigo = ncm.get("codigoNcm")
        if not codigo:
            continue
        vistas.setdefault(codigo, ncm)
    return list(vistas.values())


def vinculos_de(ncm):
    """Os vínculos de uma NCM, descartando os que não têm código."""
    return [v for v in ncm.get("listaAtributos") or [] if v.get("codigo")]


def dicionario_atributos(dados):
    """código -> detalhe do atributo (nome, tipo, domínio, órgãos)."""
    return {a["codigo"]: a for a in dados.get("detalhesAtributos") or [] if a.get("codigo")}


def nome_de(detalhe):
    return detalhe.get("nomeApresentacao") or detalhe.get("nome")


def viradas(dados, referencia=None):
    """As viradas agendadas, ordenadas pela data em que passam a valer.

    O corte é >= e não >: no dia exato da virada o vínculo tem de continuar
    aparecendo. É o dia mais acionável para quem mantém o catálogo, e era
    justamente o que sumia da página - a cópia "É hoje." nunca rodava.
    """
    ref = (referencia or hoje_br()).isoformat()
    dic = dicionario_atributos(dados)
    achados = []

    for ncm in lista_ncms(dados):
        codigo_ncm = ncm["codigoNcm"]
        for vinculo in vinculos_de(ncm):
            fim = _fim_vigencia(vinculo)
            if fim is None or fim < ref:
                continue
            # A cláusula obrigatorio == false não filtra nada hoje: todos os
            # vínculos com dataFimVigencia são opcionais. Mantida porque passa
            # a filtrar no dia em que o órgão publicar uma virada já obrigatória.
            if vinculo.get("obrigatorio") is not False:
                continue

            detalhe = dic.get(vinculo["codigo"], {})
            achados.append(
                {
                    "ncm": codigo_ncm,
                    "atributo": vinculo["codigo"],
                    "vira_obrigatorio_em": fim,
                    "vigente_desde": vinculo.get("dataInicioVigencia"),
                    "modalidade": vinculo.get("modalidade"),
                    "nome": nome_de(detalhe),
                    "orgaos": detalhe.get("orgaos") or [],
                    "forma_preenchimento": detalhe.get("formaPreenchimento"),
                }
            )

    achados.sort(key=lambda x: (x["vira_obrigatorio_em"], x["ncm"], x["atributo"]))
    return achados


MAX_DOMINIO = 120
# Domínio inteiro dos atributos que NÃO ganham página própria: ele passa a
# ser exibido dentro da página da NCM, e precisa caber lá.
MAX_DOMINIO_INLINE = 30


def detalhe_publico(detalhe):
    """Os campos do dicionário de atributos que a página usa."""
    if not detalhe:
        return {}
    dominio = detalhe.get("dominio") or []
    return {
        "dominio_total": len(dominio),
        "codigo": detalhe.get("codigo"),
        "nome": nome_de(detalhe),
        "definicao": detalhe.get("definicao"),
        "orientacao": detalhe.get("orientacaoPreenchimento"),
        "forma_preenchimento": detalhe.get("formaPreenchimento"),
        "orgaos": detalhe.get("orgaos") or [],
        "multivalorado": detalhe.get("multivalorado"),
        # dominio[].codigo é string e preserva zero à esquerda ("01").
        # Truncado: ATT_14500 ("Município do destino final") tem 5.570 opções,
        # que renderizariam uma página de 187 mil caracteres.
        "dominio": [
            {"codigo": d.get("codigo"), "descricao": d.get("descricao")}
            for d in dominio[:MAX_DOMINIO]
        ],
    }


def ncms_afetadas(dados, lista_viradas):
    """Ficha completa de cada NCM que tem virada agendada.

    Mostra TODOS os atributos da NCM, marcando quais viram obrigatórios -
    o importador precisa ver o contexto, não só a linha que muda.
    """
    alvo = {v["ncm"] for v in lista_viradas}
    virando = {(v["ncm"], v["atributo"]): v["vira_obrigatorio_em"] for v in lista_viradas}
    dic = dicionario_atributos(dados)
    saida = []

    for ncm in lista_ncms(dados):
        codigo = ncm["codigoNcm"]
        if codigo not in alvo:
            continue
        atributos = []
        for vinculo in vinculos_de(ncm):
            detalhe = dic.get(vinculo["codigo"], {})
            atributos.append(
                {
                    "codigo": vinculo["codigo"],
                    "nome": nome_de(detalhe),
                    "obrigatorio": vinculo.get("obrigatorio"),
                    "modalidade": vinculo.get("modalidade"),
                    "orgaos": detalhe.get("orgaos") or [],
                    "vira_obrigatorio_em": virando.get((codigo, vinculo["codigo"])),
                }
            )
        atributos.sort(
            key=lambda a: (
                a["vira_obrigatorio_em"] is None,
                not a["obrigatorio"],
                a["codigo"],
            )
        )
        saida.append({"ncm": codigo, "atributos": atributos})

    saida.sort(key=lambda x: x["ncm"])
    return saida


# Números longos dentro da definição são o código da NCM. Trocados por "#"
# para que "...destaques de exportação da NCM 01012100" e a mesma frase com
# outra NCM contem como a MESMA prosa - que é o que elas são.
RE_NUMERO_LONGO = re.compile(r"\d{4,}")


def assinatura_prosa(detalhe):
    """O texto que a página mostraria, sem o número da NCM."""
    if not detalhe:
        return ("", "", "")
    return (
        (nome_de(detalhe) or "").strip(),
        RE_NUMERO_LONGO.sub("#", (detalhe.get("definicao") or "").strip()),
        (detalhe.get("orientacaoPreenchimento") or "").strip(),
    )


def merece_pagina(detalhe, total_ncms, repeticoes_da_prosa):
    """O filtro de qualidade das páginas por atributo.

    A versão anterior exigia apenas que EXISTISSE prosa, e por isso não fazia
    o que o próprio comentário dela prometia: "Escolher apenas um Destaque"
    são 31 caracteres idênticos em 586 registros, e passavam os 586.

    O corte agora é estrutural, não estilístico: um atributo que vale para
    UMA única NCM e cuja prosa é boilerplate repetido não tem o que dizer
    numa URL própria - tudo o que ele mostra já aparece na página daquela
    NCM, agora que existe uma página para cada NCM.
    """
    if not detalhe:
        return False
    if total_ncms == 1 and repeticoes_da_prosa > 1:
        return False
    return True


def indice_por_atributo(dados):
    """código do atributo -> lista de NCMs vinculadas."""
    por_atributo = {}
    for ncm in lista_ncms(dados):
        for vinculo in vinculos_de(ncm):
            por_atributo.setdefault(vinculo["codigo"], []).append(ncm["codigoNcm"])
    return por_atributo


def atributos_publicaveis(dados, lista_viradas, max_ncms=60):
    """Todo atributo que merece página própria.

    Entram, sempre: os das viradas e os citados por NCM afetada - senão as
    páginas daquelas NCMs linkariam para o vazio. Os demais entram pelo
    filtro de qualidade.
    """
    dic = dicionario_atributos(dados)
    por_atributo = indice_por_atributo(dados)
    repeticoes = collections.Counter(assinatura_prosa(d) for d in dic.values())

    nas_viradas = {v["atributo"] for v in lista_viradas}
    obrigatorios = set(nas_viradas)
    alvo = {v["ncm"] for v in lista_viradas}
    for ncm in lista_ncms(dados):
        if ncm["codigoNcm"] in alvo:
            obrigatorios |= {v["codigo"] for v in vinculos_de(ncm)}

    saida = []
    for codigo, ncms in sorted(por_atributo.items()):
        detalhe = dic.get(codigo)
        if codigo not in obrigatorios:
            if not merece_pagina(detalhe, len(ncms), repeticoes[assinatura_prosa(detalhe)]):
                continue
        item = detalhe_publico(detalhe)
        item.update(
            {
                "codigo": codigo,
                "total_ncms": len(ncms),
                "ncms": sorted(ncms)[:max_ncms],
                "nas_viradas": codigo in nas_viradas,
            }
        )
        saida.append(item)
    return saida


def mapa_completo(dados, com_pagina):
    """NCM -> atributos, para gerar uma página por NCM.

    Não é versionado: são ~73 mil vínculos que mudam todo dia. O gerador
    consome no mesmo run do CI e o arquivo morre com o runner.

    Quem NÃO tem página própria viaja com orientação e domínio embutidos -
    o conteúdo não some, muda de lugar: passa a ser exibido dentro da
    página da NCM.
    """
    dic = dicionario_atributos(dados)
    ncms = {}
    usados = set()
    for ncm in lista_ncms(dados):
        vinculos = vinculos_de(ncm)
        if not vinculos:
            continue
        ncms[ncm["codigoNcm"]] = [
            [v["codigo"], v.get("obrigatorio"), v.get("modalidade")] for v in vinculos
        ]
        usados |= {v["codigo"] for v in vinculos}

    atributos = {}
    for codigo in sorted(usados):
        detalhe = dic.get(codigo) or {}
        item = {"n": nome_de(detalhe), "o": detalhe.get("orgaos") or []}
        if codigo not in com_pagina:
            orientacao = (detalhe.get("orientacaoPreenchimento") or "").strip()
            dominio = detalhe.get("dominio") or []
            if orientacao:
                item["t"] = orientacao
            if dominio:
                item["d"] = [
                    [d.get("codigo"), d.get("descricao")]
                    for d in dominio[:MAX_DOMINIO_INLINE]
                ]
                item["dt"] = len(dominio)
        atributos[codigo] = item

    return {"versao": dados.get("versao"), "ncms": ncms, "atributos": atributos}


def orgaos(atributos):
    """Um agrupamento por órgão anuente.

    Cria um eixo de navegação e de consulta novo ("atributos anvisa duimp") e
    evita um índice único com centenas de itens.
    """
    por_orgao = {}
    for a in atributos:
        for orgao in a.get("orgaos") or ["Sem órgão declarado"]:
            por_orgao.setdefault(orgao, []).append(
                {
                    "codigo": a["codigo"],
                    "nome": a.get("nome"),
                    "forma_preenchimento": a.get("forma_preenchimento"),
                    "total_ncms": a.get("total_ncms", 0),
                    "nas_viradas": a.get("nas_viradas", False),
                }
            )
    saida = []
    usados = set()
    for orgao, lista in sorted(por_orgao.items()):
        # Dois órgãos que só diferem na pontuação ("MIN.DEFESA" e "MIN DEFESA")
        # colidiriam no mesmo slug e uma página sobrescreveria a outra.
        base = slug(orgao)
        s, n = base, 2
        while s in usados:
            s, n = f"{base}-{n}", n + 1
        usados.add(s)
        lista.sort(key=lambda x: (-x["total_ncms"], x["codigo"]))
        saida.append(
            {
                "orgao": orgao,
                "slug": s,
                "total_atributos": len(lista),
                "atributos": lista,
            }
        )
    saida.sort(key=lambda x: -x["total_atributos"])
    return saida


def contagens(dados, referencia=None):
    """Números de controle. Alimentam o portão de sanidade."""
    ncms = lista_ncms(dados)
    brutos = [v for n in ncms for v in n.get("listaAtributos") or []]
    vinculos = [v for v in brutos if v.get("codigo")]
    hoje = (referencia or hoje_br()).isoformat()
    return {
        "versao": dados.get("versao"),
        "ncms": len(ncms),
        "ncms_sem_atributo": sum(1 for n in ncms if not vinculos_de(n)),
        "atributos_distintos": len({v["codigo"] for v in vinculos}),
        "detalhes_atributos": len(dicionario_atributos(dados)),
        "vinculos": len(vinculos),
        "descartados": len(brutos) - len(vinculos),
        "obrigatorios": sum(1 for v in vinculos if v.get("obrigatorio") is True),
        # Presente mas não booleano ("false" como string): é o caso em que
        # viradas() zera em silêncio. None conta como ausente.
        "obrigatorio_nao_booleano": sum(
            1
            for v in vinculos
            if v.get("obrigatorio") is not None
            and not isinstance(v.get("obrigatorio"), bool)
        ),
        "com_fim_vigencia": sum(1 for v in vinculos if _fim_vigencia(v)),
        # Só os que ainda não venceram: é o número que tem de bater com
        # len(viradas) enquanto todo vínculo com data for opcional.
        "com_fim_vigencia_futuro": sum(
            1 for v in vinculos if (_fim_vigencia(v) or "") >= hoje
        ),
        "datas_invalidas": len(datas_invalidas(dados)),
        "inicio_vigencia_futuro": sum(
            1 for v in vinculos if (v.get("dataInicioVigencia") or "") > hoje
        ),
    }


# Piso absoluto, para a primeira execução, quando não há snapshot anterior
# com que comparar. Bem abaixo do real: serve para pegar catástrofe, não
# para pegar variação. "obrigatorios" entra porque é o número que zera
# quando o campo muda de tipo - e aí o site publica "nada agendado".
PISO = {"ncms": 5000, "vinculos": 30000, "atributos_distintos": 500, "obrigatorios": 5000}
# As contagens comparadas com o snapshot anterior (base rolante).
ROLANTES = ("ncms", "vinculos", "atributos_distintos", "obrigatorios")
# Quanto a base pode encolher de um dia para o outro antes de virar suspeita.
QUEDA_MAXIMA = 0.10
# Fração dos vínculos com dataFimVigencia inválida a partir da qual o
# arquivo inteiro é suspeito, e não só uma linha digitada errado.
LIMITE_DATAS_INVALIDAS = 0.001


def conferir_sanidade(atual, anterior=None, aceitar_queda=False):
    """Levanta se a colheita estiver degenerada. Roda ANTES de gravar.

    Sem isto, um ZIP válido com listaNcm vazia derruba atributos.json de
    1 MB para 85 bytes, o site de 918 páginas para 5, e o workflow commita e
    publica tudo com exit 0. O endpoint ignora ?data=: o dia não volta.

    Com aceitar_queda=True (a válvula SENTINELA_ACEITAR_QUEDA=1) a queda
    rolante vira aviso em vez de erro - para o dia em que a Receita de fato
    encolher a base. Os pisos absolutos, a versão, os detalhes vazios e as
    datas inválidas em massa continuam fatais: esses não são "a base mudou",
    são "o arquivo veio quebrado". Devolve a lista das quedas aceitas.
    """
    fatais = []
    if not atual.get("versao"):
        fatais.append("versao ausente ou vazia")
    if atual.get("detalhes_atributos", 0) == 0:
        fatais.append("detalhesAtributos vazio")

    for chave, piso in PISO.items():
        if atual.get(chave, 0) < piso:
            fatais.append(f"{chave}={atual.get(chave)} abaixo do piso {piso}")

    nao_booleanos = atual.get("obrigatorio_nao_booleano", 0)
    if nao_booleanos:
        fatais.append(f"obrigatorio não booleano em {nao_booleanos} vínculos")

    invalidas = atual.get("datas_invalidas", 0)
    if invalidas and invalidas > atual.get("vinculos", 0) * LIMITE_DATAS_INVALIDAS:
        fatais.append(
            f"datas_invalidas={invalidas}, acima de "
            f"{LIMITE_DATAS_INVALIDAS:.1%} dos vínculos"
        )

    quedas = []
    if anterior:
        for chave in ROLANTES:
            antes, agora = anterior.get(chave), atual.get(chave)
            if not antes or agora is None:
                continue
            if agora < antes * (1 - QUEDA_MAXIMA):
                quedas.append(
                    f"{chave} caiu de {antes} para {agora} (mais de {QUEDA_MAXIMA:.0%})"
                )
    if quedas and not aceitar_queda:
        fatais.extend(quedas)
        quedas = []

    if fatais:
        raise RuntimeError("colheita degenerada, nada foi gravado: " + "; ".join(fatais))
    return quedas


def snapshot_anterior(caminho=ARQ_ULTIMO):
    """O último snapshot gravado, ou None.

    Ilegível vira aviso, não erro: o portão perde a base rolante por um dia,
    mas os pisos absolutos continuam valendo - e abortar aqui deixaria o
    arquivo quebrado no lugar para sempre.
    """
    if not os.path.exists(caminho):
        return None
    try:
        with open(caminho, encoding="utf-8") as f:
            snapshot = json.load(f)
    except (ValueError, OSError) as e:
        avisar(f"snapshot anterior ilegível ({e}); portão sem base rolante")
        return None
    if not isinstance(snapshot, dict):
        avisar("snapshot anterior não é um objeto JSON; portão sem base rolante")
        return None
    return snapshot


def _contagens_de(snapshot):
    """As contagens de um snapshot já carregado, com aviso se não houver."""
    if snapshot is None:
        return None
    c = snapshot.get("contagens")
    if not isinstance(c, dict):
        avisar("snapshot anterior sem 'contagens'; portão sem base rolante")
        return None
    return c


def contagens_anteriores(caminho=ARQ_ULTIMO):
    return _contagens_de(snapshot_anterior(caminho))


def versao_regrediu(atual, anterior):
    """True só quando as duas versões são numéricas e a nova é menor."""
    a, b = str(atual or ""), str(anterior or "")
    return a.isdigit() and b.isdigit() and int(a) < int(b)


def aceitar_queda_por_ambiente():
    """A válvula de escape, lida do ambiente para o workflow_dispatch expor."""
    return os.environ.get("SENTINELA_ACEITAR_QUEDA", "").strip() == "1"


def _reescrever_se_mudou(caminho, corpo):
    """Grava só quando o conteúdo mudou de verdade.

    Devolve True se escreveu. A comparação ignora os campos voláteis: sem
    isso o carimbo de hora garante diff todo dia e o "Nada mudou hoje." do
    workflow nunca dispara - 16 dos 18 primeiros commits do projeto não
    carregavam nada além do relógio.
    """
    anterior = ""
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            anterior = f.read()
    if _sem_volateis(corpo) == _sem_volateis(anterior.rstrip(chr(10))):
        return False
    temporario = caminho + ".tmp"
    with open(temporario, "w", encoding="utf-8", newline="\n") as f:
        f.write(corpo + chr(10))
    os.replace(temporario, caminho)
    return True


def coletar(referencia=None, dir_dados=None):
    """Baixa, apura e grava o snapshot do dia. Devolve o snapshot.

    Nada é escrito antes do portão de sanidade - nem o ZIP bruto. Chaves
    do snapshot que existem para o workflow ler: "bruto_novo" (o JSON de
    hoje difere do de ontem: vale subir o ZIP como asset de Release),
    "conteudo_identico" (é o mesmo JSON de ontem), "portao_ignorado" (uma
    queda rolante foi aceita pela válvula), "invariantes_falhas".
    """
    cam = caminhos_em(dir_dados) if dir_dados else CAMINHOS
    baixado = baixar()
    dados = carregar(baixado.conteudo)
    validar_forma(dados)
    meta = baixado.meta
    ref = referencia or hoje_br()

    # Portão de sanidade: antes de qualquer escrita.
    anterior = snapshot_anterior(cam.ultimo)
    contagens_antes = _contagens_de(anterior)
    c = contagens(dados, ref)
    quedas_aceitas = conferir_sanidade(
        c, contagens_antes, aceitar_queda=aceitar_queda_por_ambiente()
    )
    for queda in quedas_aceitas:
        avisar(f"queda aceita por SENTINELA_ACEITAR_QUEDA=1: {queda}")

    versao_antes = (contagens_antes or {}).get("versao")
    if versao_regrediu(c["versao"], versao_antes):
        avisar(f"versao regrediu de {versao_antes} para {c['versao']}")
    # O arquivo interno carrega a data de geração. Quando ela não é a de
    # hoje, o servidor ainda não regenerou - a coleta vale, mas é de ontem.
    meta["arquivo_do_dia"] = data_do_arquivo(meta.get("arquivo_interno")) == ref.isoformat()
    if not meta["arquivo_do_dia"]:
        avisar(
            f"arquivo interno {meta.get('arquivo_interno')!r} não é do dia "
            f"{ref.isoformat()}"
        )

    sha_anterior = ((anterior or {}).get("http") or {}).get("sha256_json")
    vs = viradas(dados, ref)
    invalidas = datas_invalidas(dados)
    snapshot = {
        "schema": SCHEMA,
        "coletado_em": datetime.now(FUSO).isoformat(timespec="seconds"),
        "data_referencia": ref.isoformat(),
        "fonte": URL,
        "http": meta,
        "contagens": c,
        "invariantes_falhas": [nome for nome, ok in invariantes(c) if not ok],
        "datas_invalidas_amostra": [list(x) for x in invalidas[:MAX_DATAS_LOGADAS]],
        "portao_ignorado": bool(quedas_aceitas),
        "conteudo_identico": sha_anterior is not None
        and meta["sha256_json"] == sha_anterior,
        "bruto_novo": meta["sha256_json"] != sha_anterior,
        "viradas": vs,
        "ncms_afetadas": ncms_afetadas(dados, vs),
    }

    # Daqui para baixo, escrita. O ZIP primeiro: é a evidência bruta do
    # dia, e gravar sempre (mesmo quando idêntico) mantém o arquivo no
    # disco em sincronia com o snapshot.
    os.makedirs(cam.historico, exist_ok=True)
    _gravar_bytes(cam.bruto, baixado.bruto)

    # O detalhe dos atributos sai do snapshot diário: são centenas de itens e
    # eles quase nunca mudam. Reescrever só quando o conteúdo muda mantém o
    # commit diário pequeno. O catálogo é SEMPRE recalculado, mesmo quando o
    # JSON é idêntico ao de ontem: ele é função do JSON, das viradas E da
    # regra em merece_pagina(). Pular o recálculo quando o JSON não mudava
    # deixava o arquivo em disco preso à regra antiga até a Receita publicar
    # versão nova (888 páginas de atributo em vez de 391). O recálculo custa
    # décimos de segundo; a economia de escrita já está em
    # _reescrever_se_mudou.
    publicaveis = atributos_publicaveis(dados, vs)
    catalogo = {
        "versao": dados.get("versao"),
        "atributos": publicaveis,
        "orgaos": orgaos(publicaveis),
    }
    corpo = json.dumps(catalogo, ensure_ascii=False, indent=1, sort_keys=True)
    snapshot["catalogo_reescrito"] = _reescrever_se_mudou(cam.atributos, corpo)
    snapshot["atributos_publicaveis"] = len(publicaveis)
    snapshot["orgaos"] = len(catalogo["orgaos"])

    com_pagina = {a["codigo"] for a in publicaveis}
    _gravar_json(cam.completo, mapa_completo(dados, com_pagina))

    corpo_snapshot = json.dumps(snapshot, ensure_ascii=False, indent=1)
    caminho = os.path.join(cam.historico, f"{ref.isoformat()}.json")
    escritos = [
        _reescrever_se_mudou(caminho, corpo_snapshot),
        _reescrever_se_mudou(cam.ultimo, corpo_snapshot),
    ]
    snapshot["gravado"] = any(escritos)
    return snapshot


# Invariantes de FORMA: valem para sempre, independentes de quanto o arquivo
# cresceu. As de MAGNITUDE ficam em conferir_sanidade(), com base rolante -
# uma tabela de números congelados vira ruído em dois dias. Não bloqueiam:
# vão para o snapshot ("invariantes_falhas") e viram aviso no Actions.
def invariantes(c):
    return [
        ("versao e string", isinstance(c.get("versao"), str)),
        (
            "detalhes == distintos",
            c.get("detalhes_atributos") == c.get("atributos_distintos"),
        ),
        ("nenhum registro descartado", c.get("descartados") == 0),
        ("obrigatorio sempre booleano", c.get("obrigatorio_nao_booleano") == 0),
        ("nenhuma dataFimVigencia invalida", c.get("datas_invalidas") == 0),
        ("nenhum inicio de vigencia futuro", c.get("inicio_vigencia_futuro") == 0),
    ]


def main():
    try:
        snapshot = coletar()
    except urllib.error.HTTPError as e:
        print(f"ERRO HTTP {e.code}: {e.reason}", file=sys.stderr)
        if e.code == 406:
            print(
                "406 = header Accept errado. O endpoint só serve application/zip.",
                file=sys.stderr,
            )
        return 1
    except urllib.error.URLError as e:
        print(f"ERRO de rede: {e.reason}", file=sys.stderr)
        return 1
    except (http.client.HTTPException, OSError) as e:
        # IncompleteRead, ConnectionResetError, disco cheio: nenhum é
        # URLError, e sem isto o dia terminava num traceback cru.
        print(f"ERRO de rede ou de disco: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError, zipfile.BadZipFile) as e:
        print(f"ERRO: {e}", file=sys.stderr)
        return 1

    c = snapshot["contagens"]
    v = snapshot["viradas"]
    print(
        f"Coletado em {snapshot['coletado_em']} | versao {c['versao']} | "
        f"{snapshot['http']['bytes_zip']} bytes zip -> "
        f"{snapshot['http']['bytes_json']} bytes json"
    )
    print(f"{c['ncms']} NCMs, {c['vinculos']} vinculos, {c['obrigatorios']} obrigatorios")
    print()
    print("Invariantes:")
    for nome, ok in invariantes(c):
        print(f"  {'ok  ' if ok else 'FALHA'} {nome}")
    for nome in snapshot.get("invariantes_falhas", []):
        avisar(f"invariante violada: {nome}")
    for ncm, atributo, valor in snapshot.get("datas_invalidas_amostra", []):
        avisar(f"dataFimVigencia inválida na NCM {ncm}, atributo {atributo}: {valor!r}")
    restantes = c.get("datas_invalidas", 0) - MAX_DATAS_LOGADAS
    if restantes > 0:
        avisar(f"... e mais {restantes} datas inválidas")
    # Há fim de vigência FUTURO no arquivo mas nenhuma virada: algo na forma
    # mudou e o filtro de viradas() deixou de enxergar. As datas já vencidas
    # ficam de fora - senão o aviso dispararia todo dia depois de um corte.
    futuros = c.get("com_fim_vigencia_futuro", 0)
    if futuros > 0 and not v:
        avisar(
            f"{futuros} vínculos com dataFimVigencia futura e nenhuma "
            "virada agendada: confira se o filtro ainda enxerga o arquivo"
        )
    print()
    if snapshot.get("conteudo_identico"):
        print("JSON idêntico ao da coleta anterior.")
    situacao = "reescrito" if snapshot.get("catalogo_reescrito") else "inalterado"
    print(
        f"{snapshot.get('atributos_publicaveis', 0)} atributos publicaveis em "
        f"{snapshot.get('orgaos', 0)} orgaos (catalogo {situacao})"
    )
    if not snapshot.get("gravado"):
        print("Nada mudou hoje: snapshot identico ao anterior, nada reescrito.")
    print()
    print(f"{len(v)} viradas agendadas (obrigatorio=false E dataFimVigencia >= hoje):")
    for x in v:
        orgao = "/".join(x["orgaos"]) or "-"
        print(
            f"  {x['vira_obrigatorio_em']}  {x['ncm']}  {x['atributo']:<12} "
            f"{orgao:<10} {x['nome'] or ''}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
