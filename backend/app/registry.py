"""Registro de tipos de bloco: internos (no código) + personalizados (no banco), com versões."""

from __future__ import annotations

from .blocks.builtin import TIPOS_INTERNOS
from .models import BlockType
from .store import Store


class Registro:
    def __init__(self, store: Store) -> None:
        self.store = store

    def resolver(self, type_id: str, version: int) -> BlockType | None:
        interno = TIPOS_INTERNOS.get((type_id, version))
        if interno is not None:
            return interno
        if type_id.startswith("custom."):
            return self.store.obter_tipo(type_id, version)
        return None

    def ultima_versao(self, type_id: str) -> int | None:
        versoes = [v for (i, v) in TIPOS_INTERNOS if i == type_id]
        if versoes:
            return max(versoes)
        return self.store.ultima_versao(type_id) if type_id.startswith("custom.") else None

    def listar(self, todas_versoes: bool = False) -> list[BlockType]:
        internos = list(TIPOS_INTERNOS.values())
        return internos + self.store.listar_tipos(todas_versoes)
