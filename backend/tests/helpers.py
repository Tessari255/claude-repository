"""Construtores de fluxos e utilitários compartilhados pelos testes."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.blocks.builtin import TIPOS_INTERNOS
from app.models import BlockType, Flow
from app.validation import analisar

EXEMPLOS = Path(__file__).resolve().parents[2] / "examples"


def bloco(id: str, tipo: str, params: dict | None = None, *, versao: int = 1, x: float = 0, y: float = 0,
          label: str | None = None) -> dict[str, Any]:
    return {"id": id, "type": tipo, "version": versao, "position": {"x": x, "y": y},
            "params": params or {}, "label": label}


def con(id: str, origem: str, porta_origem: str, destino: str, porta_destino: str) -> dict[str, Any]:
    return {"id": id, "source": {"block": origem, "port": porta_origem},
            "target": {"block": destino, "port": porta_destino}}


def fluxo(blocos: list[dict], conexoes: list[dict] | None = None) -> dict[str, Any]:
    return {"schema_version": 1, "blocks": blocos, "connections": conexoes or [], "viewport": None}


def constante(id: str, tipo: str, valor: Any, **kw: Any) -> dict[str, Any]:
    return bloco(id, "builtin.constante", {"tipo": tipo, "valor": valor}, **kw)


def saida(id: str = "saida", titulo: str = "Resultado", **kw: Any) -> dict[str, Any]:
    return bloco(id, "builtin.saida", {"titulo": titulo}, **kw)


def resolver_builtin(type_id: str, version: int) -> BlockType | None:
    return TIPOS_INTERNOS.get((type_id, version))


def analisar_dict(flow: dict, resolver=resolver_builtin, **kw: Any):
    return analisar(Flow.model_validate(flow), resolver, **kw)


def codigos(analise, *, scope: str | None = None) -> list[str]:
    return [i.code for i in analise.issues if scope is None or i.scope == scope]


def carregar_exemplo(nome: str) -> dict[str, Any]:
    return json.loads((EXEMPLOS / nome).read_text(encoding="utf-8"))


def aguardar(client, run_id: str, timeout: float = 40.0) -> dict[str, Any]:
    """Consulta a execução até ela terminar (a API executa em segundo plano)."""
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        r = client.get(f"/api/execucoes/{run_id}")
        assert r.status_code == 200, r.text
        corpo = r.json()
        if corpo["state"] in ("concluido", "falhou"):
            return corpo
        time.sleep(0.1)
    raise AssertionError("a execução não terminou a tempo")


def etapas(execucao: dict) -> dict[str, dict]:
    return {e["block_id"]: e for e in execucao["steps"]}
