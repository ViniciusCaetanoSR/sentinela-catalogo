"""Apoio dos testes: ambiente em diretório temporário e rede proibida.

Três arquivos de teste remendavam sete constantes de módulo cada um para
apontar o coletor e o gerador a um diretório temporário. Com comum.Caminhos
isso virou um objeto; aqui está o jeito único de montá-lo, com os dados
produzidos pelo MESMO caminho do código de produção (apurar + gravar) em vez
de um snapshot remontado à mão, que já tinha divergido do real uma vez.
"""

import contextlib
import hashlib
import json
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import coletor  # noqa: E402
import comum  # noqa: E402

DIR_TESTES = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(DIR_TESTES, "fixtures", "amostra.json")
TEMPLATES_REAIS = comum.padrao().templates
FONTES_REAIS = comum.padrao().fontes

# A fixture tem 9 NCMs: os pisos de produção nunca passariam. O portão
# continua rodando - só a régua muda.
PISO_BAIXO = {"ncms": 1, "vinculos": 1, "atributos_distintos": 1, "obrigatorios": 1}

CONFIG_PADRAO = {
    "base_url": "https://exemplo.test",
    "base_path": "/repo",
    "contato_email": "a@b.test",
}


def amostra():
    """A fixture, carregada de novo a cada chamada: os testes a alteram."""
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


# O atributo da virada em massa sintética: opcional, com a mesma data em
# muitas NCMs - o ATT_15540 (cClassTrib) da fixture.
ATT_LOTE = "ATT_LOTE"
DATA_LOTE = "2027-01-01"


def _detalhe_lote(codigo):
    return {
        "codigo": codigo,
        "nomeApresentacao": "Código de classificação tributária",
        "nome": "cClassTrib",
        "definicao": "Classificação tributária do produto na reforma tributária.",
        "orientacaoPreenchimento": "Informe o código da tabela oficial.",
        "formaPreenchimento": "TEXTO",
        "orgaos": ["RFB"],
        "multivalorado": False,
        "dominio": [],
    }


def _vinculo_lote(codigo, data):
    return {
        "codigo": codigo,
        "obrigatorio": False,
        "modalidade": "IMPORTACAO",
        "dataInicioVigencia": "2026-01-01",
        "dataFimVigencia": data,
    }


def com_lote(dados, n, codigo=ATT_LOTE, data=DATA_LOTE):
    """A amostra mais N NCMs sintéticas (9001.xx.yy), cada uma com um único
    vínculo: o mesmo atributo opcional, com a mesma dataFimVigencia.

    É a fixture do modo lote, montada em memória para não inchar a
    amostra.json nem mexer nos casos que os outros testes contam. Devolve o
    próprio `dados`, já alterado.
    """
    for i in range(n):
        dados["listaNcm"].append(
            {
                "codigoNcm": f"9001.{i // 100:02d}.{i % 100:02d}",
                "listaAtributos": [_vinculo_lote(codigo, data)],
            }
        )
    dados["detalhesAtributos"].append(_detalhe_lote(codigo))
    return dados


def com_atributo_em_todas(dados, codigo=ATT_LOTE, data=DATA_LOTE):
    """O mesmo atributo opcional, com a mesma data, em TODAS as NCMs da
    amostra - o cenário cClassTrib em miniatura. Devolve `dados`."""
    for ncm in dados["listaNcm"]:
        if ncm.get("codigoNcm"):
            ncm.setdefault("listaAtributos", []).append(_vinculo_lote(codigo, data))
    dados["detalhesAtributos"].append(_detalhe_lote(codigo))
    return dados


@contextlib.contextmanager
def ambiente(tmp, cfg=None, com_templates=True):
    """Um repositório de mentira em `tmp`, com os templates e fontes reais.

    Devolve o comum.Caminhos dele. Dentro do bloco o portão usa PISO_BAIXO e
    a válvula do ambiente está fechada, para um SENTINELA_ACEITAR_QUEDA=1
    esquecido no shell não mudar o resultado de um teste. com_templates=False
    pula a cópia dos templates e fontes - os testes do coletor não os usam,
    e são dezenas de ambientes por arquivo.
    """
    caminhos = comum.Caminhos(raiz=tmp)
    os.makedirs(caminhos.historico, exist_ok=True)
    # Cópia, não link: link simbólico pede privilégio no Windows.
    if com_templates and not os.path.isdir(caminhos.templates):
        shutil.copytree(TEMPLATES_REAIS, caminhos.templates)
    if com_templates and not os.path.isdir(caminhos.fontes) and os.path.isdir(FONTES_REAIS):
        shutil.copytree(FONTES_REAIS, caminhos.fontes)
    with open(caminhos.config, "w", encoding="utf-8") as f:
        json.dump(dict(CONFIG_PADRAO, **(cfg or {})), f)
    with (
        mock.patch.object(coletor, "PISO", PISO_BAIXO),
        mock.patch.dict(os.environ, {"SENTINELA_ACEITAR_QUEDA": ""}),
    ):
        yield caminhos


def meta_de(dados, referencia):
    """Os metadados que baixar() registraria se este JSON tivesse vindo da rede.

    O sha256 é o do JSON serializado: dois runs com a mesma fixture saem com
    conteudo_identico=True, como na produção.
    """
    conteudo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
    return {
        "status": 200,
        "content_type": "application/zip",
        # Não há ZIP neste caminho; None diz isso em vez de inventar um número.
        "bytes_zip": None,
        "disposition": None,
        "arquivo_interno": f"ATRIBUTOS_POR_NCM_{referencia.strftime('%Y_%m_%d')}.json",
        "bytes_json": len(conteudo),
        "sha256_json": hashlib.sha256(conteudo).hexdigest(),
    }


def montar_dados(caminhos, dados, referencia, ajustar=None, meta_http=None):
    """Produz ultimo.json, atributos.json, completo.json e o histórico do dia
    pelo mesmo caminho do coletor (apurar + gravar), sem rede e sem ZIP.

    ajustar(snapshot, catalogo, completo) roda antes da gravação, para os
    testes que precisam de um dado deliberadamente errado. Devolve a
    Apuracao.
    """
    anterior = coletor.snapshot_anterior(caminhos.ultimo)
    meta = meta_http or meta_de(dados, referencia)
    apuracao = coletor.apurar(dados, referencia, meta, anterior)
    if ajustar:
        ajustar(apuracao.snapshot, apuracao.catalogo, apuracao.completo)
    coletor.gravar(apuracao, caminhos)
    return apuracao


def proibir_rede():
    """Nenhum teste toca a rede; um que tente fica vermelho, não lento.

    Para o setUpModule de cada arquivo de teste. time.sleep vira no-op pelo
    mesmo motivo: o retry do coletor esperaria 10 s na primeira falha. Os
    patches são desfeitos no fim do módulo (addModuleCleanup).
    """
    for p in (
        mock.patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("rede proibida nos testes"),
        ),
        mock.patch("time.sleep"),
    ):
        p.start()
        unittest.addModuleCleanup(p.stop)
