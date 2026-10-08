"""Sistema de tipos dos dados que trafegam entre blocos.

Todos os valores são JSON puro. Os tipos iniciais são texto, número, booleano, lista
e objeto JSON; ``qualquer`` aceita (e produz) qualquer valor JSON e é verificado em
tempo de execução.
"""

from __future__ import annotations

import math
from typing import Any, Literal

TipoDado = Literal["texto", "numero", "booleano", "lista", "json", "qualquer"]
TIPOS_DADO: tuple[str, ...] = ("texto", "numero", "booleano", "lista", "json", "qualquer")

ROTULOS_TIPO = {
    "texto": "texto",
    "numero": "número",
    "booleano": "booleano (sim/não)",
    "lista": "lista",
    "json": "objeto JSON",
    "qualquer": "qualquer tipo",
}

PROFUNDIDADE_MAX = 64


def rotulo_tipo(tipo: str) -> str:
    return ROTULOS_TIPO.get(tipo, tipo)


def tipo_do_valor(valor: Any) -> str | None:
    """Tipo concreto de um valor JSON (None para o nulo, que não tem tipo próprio)."""
    if isinstance(valor, bool):
        return "booleano"
    if isinstance(valor, (int, float)):
        return "numero"
    if isinstance(valor, str):
        return "texto"
    if isinstance(valor, list):
        return "lista"
    if isinstance(valor, dict):
        return "json"
    return None


def valor_e_do_tipo(valor: Any, tipo: str) -> bool:
    if tipo == "qualquer":
        return True
    return tipo_do_valor(valor) == tipo


def descrever_valor(valor: Any) -> str:
    if valor is None:
        return "vazio (nulo)"
    return rotulo_tipo(tipo_do_valor(valor) or "qualquer")


def tipos_compativeis(origem: str, destino: str) -> bool:
    """Uma saída `origem` pode alimentar uma entrada `destino`?"""
    return origem == destino or destino == "qualquer" or origem == "qualquer"


def validar_json_puro(valor: Any, profundidade: int = 0) -> None:
    """Garante que o valor só contém JSON puro (sem NaN/infinito, chaves não-texto etc.)."""
    if profundidade > PROFUNDIDADE_MAX:
        raise ValueError("estrutura aninhada demais")
    if valor is None or isinstance(valor, bool):
        return
    if isinstance(valor, str):
        try:
            valor.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("texto com caracteres inválidos (surrogate solitário)") from None
        return
    if isinstance(valor, int):
        return
    if isinstance(valor, float):
        if not math.isfinite(valor):
            raise ValueError("número não finito (NaN ou infinito)")
        return
    if isinstance(valor, (list, tuple)):
        for item in valor:
            validar_json_puro(item, profundidade + 1)
        return
    if isinstance(valor, dict):
        for chave, item in valor.items():
            if not isinstance(chave, str):
                raise ValueError(f"chave {chave!r} não é texto")
            validar_json_puro(chave, profundidade + 1)
            validar_json_puro(item, profundidade + 1)
        return
    raise ValueError(f"valor do tipo {type(valor).__name__} não é JSON")


def exemplo_do_tipo(tipo: str) -> Any:
    """Valor de exemplo para preencher testes de bloco."""
    return {
        "texto": "exemplo",
        "numero": 1,
        "booleano": True,
        "lista": [1, 2, 3],
        "json": {"chave": "valor"},
        "qualquer": "exemplo",
    }.get(tipo)
