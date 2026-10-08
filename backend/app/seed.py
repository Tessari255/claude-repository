"""Projetos de exemplo criados na primeira execução (a partir de examples/*.json)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .errors import ApiError
from .exchange import importar
from .registry import Registro
from .store import Store

log = logging.getLogger("trama.seed")


def semear_exemplos(store: Store, registro: Registro, pasta: Path) -> None:
    if store.listar_projetos() or not pasta.is_dir():
        return
    for arquivo in sorted(pasta.glob("*.json")):
        try:
            res = importar(store, registro, json.loads(arquivo.read_text(encoding="utf-8")))
            store.criar_projeto(res["name"], res["description"], res["flow"])
        except (ApiError, ValueError) as exc:
            log.warning("Exemplo %s não foi carregado: %s", arquivo.name, exc)
