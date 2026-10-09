"""Persistência em SQLite: projetos, tipos de bloco personalizados e histórico de execuções.

Uma conexão por operação (SQLite em modo WAL), segura para uso com threads. Gravações
que envolvem mais de uma linha usam transação, então uma falha nunca deixa o último
estado válido pela metade.

Versões do esquema (``PRAGMA user_version``):

* 1 (ou 0, banco anterior ao controle de versão) — fluxos em grafo, histórico por bloco;
* 2 — fluxos em passos. Ao abrir um banco antigo, os fluxos são convertidos (``migracao.py``),
  o histórico de execuções antigo é descartado (ele descreve blocos que não existem mais no formato novo)
  e uma cópia do banco original é guardada ao lado, em ``trama.db.v1.bak``.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import ApiError
from .migracao import fluxo_v1, migrar_fluxo
from .models import BlockType

log = logging.getLogger("trama.store")

VERSAO_ESQUEMA = 2

ESQUEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    flow TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS block_types (
    id TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    definition TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (id, version)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    duration_ms INTEGER,
    snapshot TEXT NOT NULL,
    trigger_inputs TEXT,
    result TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS runs_por_projeto ON runs (project_id, created_at DESC);
CREATE TABLE IF NOT EXISTS run_steps (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_key TEXT NOT NULL,
    step_id TEXT NOT NULL,
    iteration TEXT NOT NULL DEFAULT '[]',
    position INTEGER NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    duration_ms INTEGER,
    inputs TEXT,
    outputs TEXT,
    logs TEXT,
    error TEXT,
    skip_reason TEXT,
    PRIMARY KEY (run_id, step_key)
);
"""

CAMPOS_JSON_ETAPA = ("inputs", "outputs", "logs", "error")
CAMPOS_ETAPA = frozenset({"state", "started_at", "finished_at", "duration_ms", "inputs", "outputs", "logs", "error",
                          "skip_reason"})
CAMPOS_EXECUCAO = frozenset({"state", "started_at", "finished_at", "duration_ms", "result", "error"})


def agora() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def novo_id(prefixo: str) -> str:
    return f"{prefixo}_{uuid.uuid4().hex[:12]}"


def _dump(v: Any) -> str | None:
    # ensure_ascii=True: o texto gravado é ASCII puro (sem erro de codificação, mesmo com caracteres incomuns)
    return None if v is None else json.dumps(v, ensure_ascii=True)


def _load(v: str | None) -> Any:
    return None if v is None else json.loads(v)


def _contar_passos(passos: list[dict[str, Any]]) -> int:
    total = 0
    for p in passos:
        total += 1
        for filhos in (p.get("slots") or {}).values():
            total += _contar_passos(filhos)
    return total


def _tipos_dos_passos(passos: list[dict[str, Any]]) -> Iterator[str]:
    for p in passos:
        yield p.get("type", "")
        for filhos in (p.get("slots") or {}).values():
            yield from _tipos_dos_passos(filhos)


class Store:
    def __init__(self, caminho: Path) -> None:
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self._preparar()

    # ----------------------------------------------------------------- esquema
    def _preparar(self) -> None:
        with self._conexao() as c:
            c.execute("PRAGMA journal_mode=WAL")
            versao = c.execute("PRAGMA user_version").fetchone()[0]
            existe = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='projects'").fetchone()
        if versao >= VERSAO_ESQUEMA:
            return
        if existe:
            self._atualizar_do_formato_1()
            return
        with self._conexao() as c:
            c.executescript(ESQUEMA)
            c.execute(f"PRAGMA user_version={VERSAO_ESQUEMA}")

    def _atualizar_do_formato_1(self) -> None:
        backup = self.caminho.with_name(self.caminho.name + ".v1.bak")
        with self._conexao() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if not backup.exists():
            shutil.copy2(self.caminho, backup)
            log.warning("Banco no formato antigo: cópia de segurança salva em %s", backup)
        with self._transacao() as c:
            for r in c.execute("SELECT id, name, description, flow FROM projects").fetchall():
                try:
                    antigo = json.loads(r["flow"])
                    novo = migrar_fluxo(antigo) if fluxo_v1(antigo) else antigo
                    descricao = r["description"]
                except Exception:
                    log.exception("Não foi possível converter o projeto %s", r["id"])
                    novo = {"schema_version": 2, "steps": []}
                    descricao = (r["description"] + "\n\n" if r["description"] else "") + \
                        "O fluxo antigo não pôde ser convertido; ele está na cópia de segurança do banco (trama.db.v1.bak)."
                c.execute("UPDATE projects SET flow = ?, description = ? WHERE id = ?",
                          (_dump(novo), descricao[:1000], r["id"]))
            c.execute("DROP TABLE IF EXISTS run_steps")
            c.execute("DROP TABLE IF EXISTS runs")
        with self._conexao() as c:
            c.executescript(ESQUEMA)
            c.execute(f"PRAGMA user_version={VERSAO_ESQUEMA}")

    # ----------------------------------------------------------------- conexão
    @contextmanager
    def _conexao(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.caminho, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=15000")
        try:
            yield c
        finally:
            c.close()

    @contextmanager
    def _transacao(self) -> Iterator[sqlite3.Connection]:
        with self._conexao() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                yield c
            except BaseException:
                c.execute("ROLLBACK")
                raise
            else:
                c.execute("COMMIT")

    # ---------------------------------------------------------------- projetos
    @staticmethod
    def _projeto(r: sqlite3.Row, com_fluxo: bool = True) -> dict[str, Any]:
        d = {"id": r["id"], "name": r["name"], "description": r["description"], "revision": r["revision"],
             "created_at": r["created_at"], "updated_at": r["updated_at"]}
        if com_fluxo:
            d["flow"] = json.loads(r["flow"])
        return d

    def criar_projeto(self, nome: str, descricao: str, fluxo: dict[str, Any]) -> dict[str, Any]:
        pid, t = novo_id("prj"), agora()
        with self._transacao() as c:
            c.execute("INSERT INTO projects (id, name, description, flow, revision, created_at, updated_at) "
                      "VALUES (?, ?, ?, ?, 1, ?, ?)", (pid, nome, descricao, _dump(fluxo), t, t))
        return self.obter_projeto(pid)  # type: ignore[return-value]

    def obter_projeto(self, pid: str) -> dict[str, Any] | None:
        with self._conexao() as c:
            r = c.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
        return self._projeto(r) if r else None

    def listar_projetos(self) -> list[dict[str, Any]]:
        with self._conexao() as c:
            linhas = c.execute(
                "SELECT p.*, "
                "(SELECT state FROM runs r WHERE r.project_id = p.id AND r.kind = 'fluxo' ORDER BY r.created_at DESC LIMIT 1) AS last_state, "
                "(SELECT created_at FROM runs r WHERE r.project_id = p.id AND r.kind = 'fluxo' ORDER BY r.created_at DESC LIMIT 1) AS last_at, "
                "(SELECT COUNT(*) FROM runs r WHERE r.project_id = p.id AND r.kind = 'fluxo') AS run_count "
                "FROM projects p ORDER BY p.updated_at DESC"
            ).fetchall()
        saida = []
        for r in linhas:
            fluxo = json.loads(r["flow"])
            d = self._projeto(r, com_fluxo=False)
            d["step_count"] = _contar_passos(fluxo.get("steps", []))
            d["last_run_state"] = r["last_state"]
            d["last_run_at"] = r["last_at"]
            d["run_count"] = r["run_count"]
            saida.append(d)
        return saida

    def salvar_projeto(self, pid: str, nome: str | None, descricao: str | None,
                       fluxo: dict[str, Any] | None, base_revision: int | None) -> dict[str, Any]:
        """Atualização atômica com controle otimista de revisão."""
        with self._transacao() as c:
            r = c.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
            if r is None:
                raise ApiError(404, "projeto_nao_encontrado", "Projeto não encontrado.")
            if base_revision is not None and base_revision != r["revision"]:
                raise ApiError(
                    409, "conflito_de_revisao",
                    "Este projeto foi alterado em outra aba ou janela depois que você o abriu.",
                    sugestao="Exporte o fluxo atual para não perder o trabalho e recarregue a página para ver a versão salva.")
            c.execute(
                "UPDATE projects SET name = ?, description = ?, flow = ?, revision = revision + 1, updated_at = ? "
                "WHERE id = ?",
                (nome if nome is not None else r["name"], descricao if descricao is not None else r["description"],
                 _dump(fluxo) if fluxo is not None else r["flow"], agora(), pid))
        return self.obter_projeto(pid)  # type: ignore[return-value]

    def excluir_projeto(self, pid: str) -> bool:
        with self._transacao() as c:
            return c.execute("DELETE FROM projects WHERE id = ?", (pid,)).rowcount > 0

    def projetos_que_usam(self, type_id: str) -> list[str]:
        usados = []
        with self._conexao() as c:
            for r in c.execute("SELECT id, name, flow FROM projects").fetchall():
                fluxo = json.loads(r["flow"])
                if type_id in _tipos_dos_passos(fluxo.get("steps", [])):
                    usados.append(r["name"])
        return usados

    # ------------------------------------------------------------ tipos de bloco
    def inserir_tipo(self, tipo: BlockType) -> BlockType:
        criado = tipo.model_copy(update={"created_at": tipo.created_at or agora()})
        with self._transacao() as c:
            c.execute("INSERT INTO block_types (id, version, name, definition, created_at) VALUES (?, ?, ?, ?, ?)",
                      (criado.id, criado.version, criado.name, criado.model_dump_json(), criado.created_at))
        return criado

    def inserir_tipos(self, tipos: list[BlockType]) -> list[BlockType]:
        """Insere vários tipos em uma única transação (tudo ou nada)."""
        criados = [t.model_copy(update={"created_at": t.created_at or agora()}) for t in tipos]
        with self._transacao() as c:
            for t in criados:
                c.execute("INSERT INTO block_types (id, version, name, definition, created_at) VALUES (?, ?, ?, ?, ?)",
                          (t.id, t.version, t.name, t.model_dump_json(), t.created_at))
        return criados

    def inserir_nova_versao(self, tipo: BlockType) -> BlockType:
        """Cria a próxima versão de `tipo.id` de forma atômica (várias edições simultâneas não colidem)."""
        with self._transacao() as c:
            r = c.execute("SELECT MAX(version) AS v FROM block_types WHERE id = ?", (tipo.id,)).fetchone()
            if r["v"] is None:
                raise ApiError(404, "bloco_nao_encontrado", "Bloco não encontrado.")
            criado = tipo.model_copy(update={"version": r["v"] + 1, "created_at": agora()})
            c.execute("INSERT INTO block_types (id, version, name, definition, created_at) VALUES (?, ?, ?, ?, ?)",
                      (criado.id, criado.version, criado.name, criado.model_dump_json(), criado.created_at))
        return criado

    def obter_tipo(self, type_id: str, version: int) -> BlockType | None:
        if not 0 < version <= 1_000_000:
            return None  # fora da faixa do SQLite/dos modelos: não existe
        with self._conexao() as c:
            r = c.execute("SELECT definition FROM block_types WHERE id = ? AND version = ?",
                          (type_id, version)).fetchone()
        return BlockType.model_validate_json(r["definition"]) if r else None

    def ultima_versao(self, type_id: str) -> int | None:
        with self._conexao() as c:
            r = c.execute("SELECT MAX(version) AS v FROM block_types WHERE id = ?", (type_id,)).fetchone()
        return r["v"] if r and r["v"] is not None else None

    def listar_tipos(self, todas_versoes: bool = False) -> list[BlockType]:
        with self._conexao() as c:
            if todas_versoes:
                linhas = c.execute("SELECT definition FROM block_types ORDER BY name, id, version").fetchall()
            else:
                linhas = c.execute(
                    "SELECT t.definition FROM block_types t JOIN "
                    "(SELECT id, MAX(version) v FROM block_types GROUP BY id) m ON m.id = t.id AND m.v = t.version "
                    "ORDER BY t.name").fetchall()
        return [BlockType.model_validate_json(r["definition"]) for r in linhas]

    def excluir_tipo(self, type_id: str) -> bool:
        with self._transacao() as c:
            return c.execute("DELETE FROM block_types WHERE id = ?", (type_id,)).rowcount > 0

    # ---------------------------------------------------------------- execuções
    def criar_execucao(self, *, kind: str, project_id: str | None, snapshot: dict[str, Any],
                       etapas: list[str], trigger_inputs: dict[str, Any] | None = None) -> str:
        rid = novo_id("exe")
        with self._transacao() as c:
            c.execute("INSERT INTO runs (id, project_id, kind, state, created_at, snapshot, trigger_inputs) "
                      "VALUES (?, ?, ?, 'aguardando', ?, ?, ?)",
                      (rid, project_id, kind, agora(), _dump(snapshot), _dump(trigger_inputs or {})))
            for pos, sid in enumerate(etapas):
                c.execute("INSERT INTO run_steps (run_id, step_key, step_id, iteration, position, state) "
                          "VALUES (?, ?, ?, '[]', ?, 'aguardando')", (rid, sid, sid, pos))
        return rid

    def atualizar_execucao(self, rid: str, **campos: Any) -> None:
        if not campos:
            return
        desconhecidos = set(campos) - CAMPOS_EXECUCAO
        if desconhecidos:
            raise ValueError(f"campos desconhecidos: {sorted(desconhecidos)}")
        colunas = []
        valores: list[Any] = []
        for k, v in campos.items():
            colunas.append(f"{k} = ?")
            valores.append(_dump(v) if k in ("result", "error") else v)
        with self._transacao() as c:
            c.execute(f"UPDATE runs SET {', '.join(colunas)} WHERE id = ?", (*valores, rid))

    def garantir_etapa(self, rid: str, chave: str, step_id: str, iteracao: list[int], posicao: int) -> bool:
        """Cria a linha da etapa (um passo numa repetição) se ainda não existir. True se foi criada agora."""
        with self._transacao() as c:
            return c.execute(
                "INSERT OR IGNORE INTO run_steps (run_id, step_key, step_id, iteration, position, state) "
                "VALUES (?, ?, ?, ?, ?, 'aguardando')", (rid, chave, step_id, _dump(iteracao), posicao)).rowcount > 0

    def atualizar_etapa(self, rid: str, chave: str, **campos: Any) -> None:
        if not campos:
            return
        desconhecidos = set(campos) - CAMPOS_ETAPA
        if desconhecidos:
            raise ValueError(f"campos desconhecidos: {sorted(desconhecidos)}")
        colunas = []
        valores: list[Any] = []
        for k, v in campos.items():
            colunas.append(f"{k} = ?")
            valores.append(_dump(v) if k in CAMPOS_JSON_ETAPA else v)
        with self._transacao() as c:
            c.execute(f"UPDATE run_steps SET {', '.join(colunas)} WHERE run_id = ? AND step_key = ?",
                      (*valores, rid, chave))

    def contar_etapas(self, rid: str) -> int:
        with self._conexao() as c:
            return c.execute("SELECT COALESCE(MAX(position), -1) + 1 AS n FROM run_steps WHERE run_id = ?", (rid,)).fetchone()["n"]

    def encerrar_etapas_abertas(self, rid: str, motivo: str, estado_aberto: str = "falhou") -> None:
        """Fecha o que sobrou: o que ainda aguardava vira ignorado; o que estava em andamento vira `estado_aberto`."""
        with self._transacao() as c:
            c.execute("UPDATE run_steps SET state = 'ignorado', skip_reason = ? WHERE run_id = ? AND state = 'aguardando'",
                      (motivo, rid))
            c.execute("UPDATE run_steps SET state = ?, finished_at = ? WHERE run_id = ? AND state = 'executando'",
                      (estado_aberto, agora(), rid))

    @staticmethod
    def _execucao(r: sqlite3.Row, com_snapshot: bool) -> dict[str, Any]:
        d = {"id": r["id"], "project_id": r["project_id"], "kind": r["kind"], "state": r["state"],
             "created_at": r["created_at"], "started_at": r["started_at"], "finished_at": r["finished_at"],
             "duration_ms": r["duration_ms"], "trigger_inputs": _load(r["trigger_inputs"]) or {},
             "result": _load(r["result"]), "error": _load(r["error"])}
        if com_snapshot:
            d["snapshot"] = _load(r["snapshot"])
        return d

    def obter_execucao(self, rid: str, com_snapshot: bool = False, com_fluxo: bool = False) -> dict[str, Any] | None:
        with self._conexao() as c:
            r = c.execute("SELECT * FROM runs WHERE id = ?", (rid,)).fetchone()
            if r is None:
                return None
            passos = c.execute("SELECT * FROM run_steps WHERE run_id = ? ORDER BY position", (rid,)).fetchall()
        d = self._execucao(r, com_snapshot or com_fluxo)
        if com_fluxo:  # o fluxo exatamente como foi executado (para abrir uma execução antiga no designer)
            d["flow"] = d["snapshot"].get("flow")
            if not com_snapshot:
                del d["snapshot"]
        d["steps"] = [
            {"step_id": s["step_id"], "iteration": _load(s["iteration"]) or [], "position": s["position"],
             "state": s["state"], "started_at": s["started_at"], "finished_at": s["finished_at"],
             "duration_ms": s["duration_ms"], "inputs": _load(s["inputs"]), "outputs": _load(s["outputs"]),
             "logs": _load(s["logs"]) or [], "error": _load(s["error"]), "skip_reason": s["skip_reason"]}
            for s in passos
        ]
        return d

    def listar_execucoes(self, project_id: str, limite: int = 30) -> list[dict[str, Any]]:
        with self._conexao() as c:
            linhas = c.execute("SELECT * FROM runs WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
                               (project_id, limite)).fetchall()
        return [self._execucao(r, False) for r in linhas]

    def marcar_interrompidas(self) -> int:
        """Execuções que estavam em andamento quando o servidor caiu não terminam sozinhas."""
        erro = {"code": "execucao_interrompida", "message": "A execução foi interrompida porque o servidor foi reiniciado.",
                "suggestion": "Execute o fluxo novamente.", "technical": None}
        with self._transacao() as c:
            n = c.execute("UPDATE runs SET state = 'falhou', finished_at = ?, error = ? "
                          "WHERE state IN ('aguardando', 'executando')", (agora(), _dump(erro))).rowcount
            c.execute("UPDATE run_steps SET state = 'ignorado', skip_reason = 'fluxo_interrompido' "
                      "WHERE state IN ('aguardando', 'executando')")
        return n
