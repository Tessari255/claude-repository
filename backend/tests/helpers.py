"""Construtores de fluxos (formato 2) e utilitários compartilhados pelos testes."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.blocks.builtin import TIPOS_INTERNOS
from app.models import BlockType, Flow
from app.validation import analisar

EXEMPLOS = Path(__file__).resolve().parents[2] / "examples"
FIXTURES_V1 = Path(__file__).resolve().parent / "fixtures" / "v1"


# ------------------------------------------------------------------ campos
def ref(step: str, output: str, path: str = "") -> dict[str, Any]:
    """Campo com UMA referência de conteúdo dinâmico (preserva o tipo do dado)."""
    return {"parts": [{"step": step, "output": output, "path": path}]}


def lit(valor: Any) -> dict[str, Any]:
    """Campo com valor fixo."""
    return {"value": valor}


def tpl(*partes: Any) -> dict[str, Any]:
    """Campo com texto + conteúdo dinâmico: ``tpl("Olá, ", ("gatilho", "nome"), "!")``."""
    return {"parts": [p if isinstance(p, str) else {"step": p[0], "output": p[1], "path": ""} for p in partes]}


# ------------------------------------------------------------------ passos
def passo(id: str, tipo: str, inputs: dict | None = None, params: dict | None = None, *, slots: dict | None = None,
          versao: int = 1, label: str | None = None, run_after: list[str] | None = None, retry: int = 0,
          intervalo: float = 0.0, timeout: float | None = None, note: str | None = None) -> dict[str, Any]:
    p: dict[str, Any] = {"id": id, "type": tipo, "version": versao, "label": label, "note": note,
                         "inputs": inputs or {}, "params": params or {}}
    if slots:
        p["slots"] = slots
    if run_after:
        p["run_after"] = run_after
    if retry or timeout:
        p["settings"] = {"retry": {"count": retry, "interval_s": intervalo}, "timeout_s": timeout}
    return p


def campo(id: str, tipo: str = "texto", default: Any = None, *, label: str | None = None, required: bool = False) -> dict[str, Any]:
    c: dict[str, Any] = {"id": id, "label": label or id.capitalize(), "type": tipo, "required": required}
    if default is not None:
        c["default"] = default
    return c


def gatilho(*campos: dict[str, Any]) -> dict[str, Any]:
    return {"id": "gatilho", "type": "builtin.gatilho_manual", "version": 1, "params": {"campos": list(campos)}}


def fluxo(passos: list[dict[str, Any]], campos: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"schema_version": 2, "trigger": gatilho(*(campos or [])), "steps": passos}


def compor(id: str, valor: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return passo(id, "builtin.compor", {"entrada": valor}, **kw)


def saida(id: str = "saida", titulo: str = "Resultado", valor: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    return passo(id, "builtin.saida", {"valor": valor} if valor is not None else {}, {"titulo": titulo}, **kw)


def matematica(id: str, a: dict, b: dict, operacao: str = "somar", **kw: Any) -> dict[str, Any]:
    return passo(id, "builtin.matematica", {"a": a, "b": b}, {"operacao": operacao}, **kw)


def condicao(id: str, esq: dict, op: str, dir: Any = None, *, sim: list | None = None, nao: list | None = None, **kw: Any) -> dict[str, Any]:
    regra: dict[str, Any] = {"esq": esq, "op": op}
    if dir is not None:
        regra["dir"] = dir if isinstance(dir, dict) else lit(dir)
    return passo(id, "builtin.condicao", params={"regras": [regra], "combinador": "e"},
                 slots={"sim": sim or [], "nao": nao or []}, **kw)


def python_inline(id: str, codigo: str, entradas: dict[str, tuple[str, dict]] | None = None,
                  saidas: dict[str, str] | None = None, **kw: Any) -> dict[str, Any]:
    """Passo de código Python inline. ``entradas``: ``{"nome": ("texto", <campo>)}``; ``saidas``: ``{"mensagem": "texto"}``."""
    entradas = entradas or {}
    saidas = saidas if saidas is not None else {"resultado": "texto"}
    return passo(
        id, "builtin.python", {k: v[1] for k, v in entradas.items()},
        {"entradas": [{"id": k, "label": k.capitalize(), "type": v[0], "required": True} for k, v in entradas.items()],
         "saidas": [{"id": k, "label": k.capitalize(), "type": t} for k, t in saidas.items()], "codigo": codigo}, **kw)


# ------------------------------------------------------------------ análise e leitura
def resolver_builtin(type_id: str, version: int) -> BlockType | None:
    return TIPOS_INTERNOS.get((type_id, version))


def analisar_dict(flow: dict, resolver=resolver_builtin, **kw: Any):
    return analisar(Flow.model_validate(flow), resolver, **kw)


def codigos(analise, *, scope: str | None = None, severity: str | None = None) -> list[str]:
    return [i.code for i in analise.issues
            if (scope is None or i.scope == scope) and (severity is None or i.severity == severity)]


def carregar_exemplo(nome: str) -> dict[str, Any]:
    return json.loads((EXEMPLOS / nome).read_text(encoding="utf-8"))


def carregar_v1(nome: str) -> dict[str, Any]:
    return json.loads((FIXTURES_V1 / nome).read_text(encoding="utf-8"))


def aguardar(client, run_id: str, timeout: float = 40.0) -> dict[str, Any]:
    """Consulta a execução até ela terminar (a API executa em segundo plano)."""
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        r = client.get(f"/api/execucoes/{run_id}")
        assert r.status_code == 200, r.text
        corpo = r.json()
        if corpo["state"] in ("concluido", "falhou", "cancelado"):
            return corpo
        time.sleep(0.1)
    raise AssertionError("a execução não terminou a tempo")


def etapas(execucao: dict) -> dict[str, dict]:
    """Linhas de histórico por passo (as repetições de um laço ficam em ``repeticoes``)."""
    return {e["step_id"]: e for e in execucao["steps"] if not e["iteration"]}


def repeticoes(execucao: dict, step_id: str) -> dict[tuple[int, ...], dict]:
    return {tuple(e["iteration"]): e for e in execucao["steps"] if e["step_id"] == step_id and e["iteration"]}


def estados(execucao: dict) -> dict[str, str]:
    return {k: v["state"] for k, v in etapas(execucao).items()}
