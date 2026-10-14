"""Preparo de uma execução: valida TUDO antes de executar, confere os dados do gatilho e congela o snapshot.

O snapshot guarda o fluxo e as definições de bloco (com a versão fixada em cada passo), então editar um bloco ou o
projeto depois não altera execuções que já foram criadas.
"""

from __future__ import annotations

from typing import Any

from .config import Limites
from .errors import ApiError
from .historico import Historico
from .models import BlockType, Flow
from .passos import percorrer
from .registry import Registro
from .sandbox import DockerExecutor
from .tipos import descrever_valor, rotulo_tipo, valor_e_do_tipo
from .validation import CONTEINERES_DE_LACO, analisar


def preparar_execucao(flow: Flow, project_id: str | None, dados_gatilho: dict[str, Any] | None, *, registro: Registro,
                      executor: DockerExecutor, limites: Limites, historico: Historico) -> str:
    """Valida o fluxo e os dados do gatilho e cria o registro da execução (estado: aguardando)."""
    analise = analisar(flow, registro.resolver, sandbox=executor.status(), ultima_versao=None, limites=limites)
    if analise.erros:
        raise ApiError(422, "fluxo_invalido",
                       "O fluxo tem problemas e não foi executado. Corrija os itens abaixo e tente de novo.",
                       problemas=[i.model_dump() for i in analise.erros])
    dados = conferir_dados_do_gatilho(analise.efetivas[flow.trigger.id], dados_gatilho or {})
    passos = [flow.trigger] + [pos.passo for pos in percorrer(flow.steps)]
    em_laco = {pos.passo.id for pos in percorrer(flow.steps)
               if any(analise.defs[a].id in CONTEINERES_DE_LACO for a in pos.ancestrais if a in analise.defs)}
    definicoes = {f"{p.type}@{p.version}": analise.defs[p.id].model_dump() for p in passos}
    snapshot = {"flow": flow.model_dump(), "definitions": definicoes, "port_types": analise.port_types,
                "trigger_inputs": dados}
    return historico.criar_execucao(kind="fluxo", project_id=project_id, snapshot=snapshot,
                                    etapas=[p.id for p in passos if p.id not in em_laco], trigger_inputs=dados)


def conferir_dados_do_gatilho(ef: BlockType, dados: dict[str, Any]) -> dict[str, Any]:
    """Os dados informados ao gatilho batem com os campos dele? Junta todos os problemas numa resposta só."""
    problemas: list[dict[str, Any]] = []
    campos = {o.id: o for o in ef.outputs}
    for chave, valor in dados.items():
        c = campos.get(chave)
        if c is None:
            problemas.append({"code": "dados_invalidos", "severity": "erro", "scope": "configuracao",
                              "message": f"O gatilho não tem o campo “{chave}”.", "field": chave})
        elif not valor_e_do_tipo(valor, c.type):
            problemas.append({"code": "dados_invalidos", "severity": "erro", "scope": "configuracao",
                              "message": f"O campo “{c.label}” deveria ser {rotulo_tipo(c.type)}, mas recebeu {descrever_valor(valor)}.",
                              "field": chave})
    for c in ef.outputs:
        if c.required and c.default is None and c.id not in dados:
            problemas.append({"code": "dados_invalidos", "severity": "erro", "scope": "configuracao",
                              "message": f"Preencha o campo “{c.label}” do gatilho.", "field": c.id})
    if problemas:
        raise ApiError(422, "dados_invalidos", "Os dados informados ao gatilho não são válidos.", problemas=problemas,
                       sugestao="Confira os campos do gatilho e tente de novo.")
    return dados
