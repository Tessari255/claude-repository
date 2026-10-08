"""Erros do domínio e da API, sempre com mensagem compreensível em português."""

from __future__ import annotations

from typing import Any


class ErroBloco(Exception):
    """Falha prevista durante a execução de um bloco (mensagem pensada para iniciantes)."""

    def __init__(self, mensagem: str, *, codigo: str = "erro_bloco",
                 sugestao: str | None = None, tecnico: dict[str, Any] | None = None) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.codigo = codigo
        self.sugestao = sugestao
        self.tecnico = tecnico

    def como_dict(self) -> dict[str, Any]:
        return {"code": self.codigo, "message": self.mensagem,
                "suggestion": self.sugestao, "technical": self.tecnico}


class ApiError(Exception):
    def __init__(self, status: int, codigo: str, mensagem: str, *,
                 problemas: list[dict[str, Any]] | None = None, sugestao: str | None = None) -> None:
        super().__init__(mensagem)
        self.status = status
        self.codigo = codigo
        self.mensagem = mensagem
        self.problemas = problemas or []
        self.sugestao = sugestao

    def corpo(self) -> dict[str, Any]:
        return {"error": {"code": self.codigo, "message": self.mensagem,
                          "suggestion": self.sugestao, "issues": self.problemas}}
