"""
O que os scripts têm em comum: caminhos, escrita atômica e config.json.

coletor.py, gerar_site.py, indexnow.py e servir.py não se importam entre si
de propósito - cada um roda sozinho e um erro num não pode derrubar o
outro. O que eles dividiam era código copiado: três versões de "grava no
.tmp e troca com os.replace", duas leituras de config.json, duas cópias da
string do User-Agent. Este módulo é o lugar único dessas coisas. Nada aqui
toca a rede nem conhece o formato do catálogo.

Sem dependências externas. Somente biblioteca padrão.
"""

import dataclasses
import json
import os
import sys

__version__ = "0.1.0"

# Identifica o projeto para quem olhar o log do servidor (da Receita e do
# IndexNow). Sem isso vai "Python-urllib/3.x", que é o primeiro padrão que
# um WAF corta.
AGENTE = (
    f"SentinelaDoCatalogo/{__version__} "
    "(+https://github.com/viniciuscaetanosr/sentinela-catalogo)"
)

RAIZ = os.path.dirname(os.path.abspath(__file__))


@dataclasses.dataclass(frozen=True)
class Caminhos:
    """Todos os arquivos e pastas que os scripts leem e gravam.

    Derivados de uma única raiz para que os testes apontem tudo para um
    diretório temporário com um único objeto, em vez de remendar sete
    constantes de módulo. `dados` e `site` podem ser trocados à parte
    (--dados e --saida na linha de comando); o resto segue a raiz.
    """

    raiz: str
    dados: str = None
    site: str = None

    def __post_init__(self):
        # frozen: os padrões derivados entram por object.__setattr__.
        if self.dados is None:
            object.__setattr__(self, "dados", os.path.join(self.raiz, "dados"))
        if self.site is None:
            object.__setattr__(self, "site", os.path.join(self.raiz, "site"))

    @property
    def historico(self):
        return os.path.join(self.dados, "historico")

    @property
    def ultimo(self):
        return os.path.join(self.dados, "ultimo.json")

    @property
    def atributos(self):
        return os.path.join(self.dados, "atributos.json")

    @property
    def completo(self):
        # Mapa completo NCM -> atributos. NÃO é versionado (ver .gitignore):
        # são ~4,8 MB (~120 KB comprimidos) que mudam todo dia e o gerador
        # consome no mesmo run do CI; o render.yml os recebe via cache ou
        # reapura do ZIP guardado em Release.
        return os.path.join(self.dados, "completo.json")

    @property
    def lastmod(self):
        return os.path.join(self.dados, "lastmod.json")

    @property
    def bruto(self):
        # O ZIP exatamente como veio da Receita. Também fora do git (*.zip):
        # o workflow guarda como artifact e, quando o conteúdo muda, como
        # asset de Release - é o que permite reapurar um dia sem bater no
        # endpoint, que não serve versões passadas.
        return os.path.join(self.dados, "bruto.zip")

    @property
    def templates(self):
        return os.path.join(self.raiz, "templates")

    @property
    def fontes(self):
        return os.path.join(self.raiz, "fontes")

    @property
    def config(self):
        return os.path.join(self.raiz, "config.json")


def padrao():
    """Os caminhos do repositório em que este arquivo está."""
    return Caminhos(raiz=RAIZ)


# ---------------------------------------------------------------- escrita


def _trocar(caminho, escrever):
    """Escreve em <caminho>.tmp e só então troca pelo definitivo.

    open(..., "w") trunca antes de escrever: um job cancelado no meio deixa
    o arquivo pela metade, e aí TODA execução seguinte quebra no json.load.
    os.replace é atômico no mesmo sistema de arquivos.
    """
    temporario = caminho + ".tmp"
    escrever(temporario)
    os.replace(temporario, caminho)


def gravar_atomico(caminho, texto):
    """Texto em UTF-8 com \\n, gravado de forma atômica."""

    def escrever(temporario):
        with open(temporario, "w", encoding="utf-8", newline="\n") as f:
            f.write(texto)

    _trocar(caminho, escrever)


def gravar_json_atomico(caminho, obj, indent=1, sort_keys=False):
    """JSON legível (indent=1, acentos intactos) com \\n no fim, atômico."""

    def escrever(temporario):
        with open(temporario, "w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent, sort_keys=sort_keys)
            f.write("\n")

    _trocar(caminho, escrever)


def gravar_bytes_atomico(caminho, dados):
    """Bytes como vieram (o ZIP bruto), gravados de forma atômica."""

    def escrever(temporario):
        with open(temporario, "wb") as f:
            f.write(dados)

    _trocar(caminho, escrever)


def ler_json_tolerante(caminho, avisar=None):
    """JSON do arquivo, ou None se ele não existe ou não dá para ler.

    Para os arquivos cuja ausência custa pouco (lastmod.json, um snapshot
    antigo do histórico, a base rolante do portão): abortar por causa deles
    deixaria o arquivo quebrado no lugar para sempre. Quando `avisar` é
    dado, recebe a descrição do problema - só quando o arquivo existe e
    está ilegível; a ausência é silenciosa.
    """
    if not os.path.exists(caminho):
        return None
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        if avisar:
            avisar(str(e))
        return None


# Campos do snapshot que mudam a cada execução sem que o dado tenha mudado.
# Ficam fora da comparação que decide se vale reescrever o arquivo - senão
# todo run produz um commit que não carrega informação nenhuma. Os quatro
# últimos são fatos sobre a execução ("o ZIP de hoje é outro", "o catálogo
# foi reescrito agora"), não sobre o catálogo - numa segunda rodada do mesmo
# dia eles mudam sem que nada tenha mudado.
VOLATEIS = (
    "coletado_em",
    "bytes_zip",
    "disposition",
    "catalogo_reescrito",
    "bruto_novo",
    "conteudo_identico",
    "portao_ignorado",
)


def _sem_volateis(texto, ignorar):
    """Ignora as linhas voláteis ao comparar - só o conteúdo importa."""
    nl = chr(10)
    return nl.join(
        linha
        for linha in texto.split(nl)
        if not any('"' + c + '"' in linha for c in ignorar)
    )


def reescrever_se_mudou(caminho, corpo, ignorar=VOLATEIS):
    """Grava só quando o conteúdo mudou de verdade. Devolve True se escreveu.

    A comparação ignora os campos voláteis: sem isso o carimbo de hora
    garante diff todo dia e o "Nada mudou hoje." do workflow nunca dispara -
    16 dos 18 primeiros commits do projeto não carregavam nada além do
    relógio. `corpo` vai sem o \\n final; ele é acrescentado na gravação.
    """
    anterior = ""
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            anterior = f.read()
    if _sem_volateis(corpo, ignorar) == _sem_volateis(anterior.rstrip(chr(10)), ignorar):
        return False
    gravar_atomico(caminho, corpo + chr(10))
    return True


# ---------------------------------------------------------------- config


# Todas opcionais; o README documenta o que acontece quando cada uma falta.
CONFIG_PADRAO = {
    "base_url": "",
    "base_path": "",
    "form_embed_url": "",
    "contato_email": "",
    "goatcounter_code": "",
    "dominio": "",
    "indexnow_key": "",
}


def carregar_config(caminho):
    """config.json sobre os padrões. Único leitor do arquivo.

    Chave desconhecida vira aviso em stderr, não erro: um "base_pth" digitado
    errado passava em silêncio e o site saía sem prefixo, com todo link
    interno em 404. Arquivo ausente é o mesmo que arquivo vazio.
    """
    cfg = dict(CONFIG_PADRAO)
    if not os.path.exists(caminho):
        return cfg
    with open(caminho, encoding="utf-8") as f:
        lido = json.load(f)
    if not isinstance(lido, dict):
        raise ValueError(f"{caminho} deveria conter um objeto JSON")
    for chave in sorted(set(lido) - set(CONFIG_PADRAO)):
        print(f"AVISO: chave desconhecida em {caminho}: {chave!r}", file=sys.stderr)
    cfg.update(lido)
    return cfg


def prefixo(cfg):
    """Em Pages de repositório de projeto o site vive sob /<repo>/."""
    return cfg.get("base_path", "").rstrip("/")


def url(cfg, caminho):
    """Link interno, já com o prefixo do base_path.

    Substitui a cirurgia de string que rodava sobre o HTML pronto
    (html.replace('href="/', ...)). Aquela versão só pegava aspas duplas, e
    por isso 17 links escritos com aspas simples saíam sem prefixo e davam
    404 - justamente nas dez páginas de atributo com virada agendada.
    """
    return prefixo(cfg) + caminho


def absoluta(cfg, caminho):
    """URL completa, para canonical, og:url, sitemap, feed e keyLocation."""
    base = cfg.get("base_url", "").rstrip("/")
    return base + prefixo(cfg) + caminho
