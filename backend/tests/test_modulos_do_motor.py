"""Módulos em que o motor foi dividido: tradução de erros, estado, histórico, conteúdo dinâmico e controle de fluxo.

O comportamento do motor inteiro está em ``test_engine.py``; aqui ficam as regras de cada peça isolada e as garantias
de arquitetura (quem importa quem, quem escreve o histórico)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from app.errors import ErroBloco
from app.erros_sandbox import NAO_REPETIR, erro_da_sandbox
from app.execucao import MAX_REGISTROS, Cancelado, Execucao, chave_etapa, falha_de
from app.historico import Historico
from app.models import Flow

from .helpers import campo, compor, fluxo, lit, passo, ref, saida

BACKEND = Path(__file__).resolve().parents[1]

# Camadas de baixo para cima; só engine.py (a fachada) pode ficar no topo.
MODULOS = ["erros_sandbox", "execucao", "historico", "engine"]


# ------------------------------------------------------------------ arquitetura
@pytest.mark.parametrize("modulo", MODULOS)
def test_cada_modulo_importa_sozinho_sem_efeito_colateral_e_sem_depender_da_fachada(modulo, tmp_path):
    codigo = (f"import sys, threading; import app.{modulo}; "
              f"assert threading.active_count() == 1, 'importar criou uma thread'; "
              f"assert {modulo!r} == 'engine' or 'app.engine' not in sys.modules, 'importou a fachada'")
    r = subprocess.run([sys.executable, "-c", codigo], cwd=tmp_path, capture_output=True, text=True, timeout=60,
                       env={**os.environ, "PYTHONPATH": str(BACKEND)})
    assert r.returncode == 0, r.stderr
    assert r.stdout == "" and r.stderr == ""
    assert list(tmp_path.iterdir()) == []  # nada de banco ou pasta criado só por importar


# ------------------------------------------------------------------ tradução dos erros do executor isolado
def test_excecao_conhecida_vira_mensagem_para_iniciantes_com_a_linha_e_o_detalhe_tecnico():
    e = erro_da_sandbox({"category": "excecao", "type": "ZeroDivisionError", "message": "division by zero",
                         "line": 3, "snippet": "x = 1 / 0", "traceback": ""})
    assert e.mensagem == "O código tentou dividir por zero (linha 3: `x = 1 / 0`)."
    assert e.codigo == "excecao_python"
    assert e.sugestao is not None and e.sugestao.startswith("Revise o código do passo")
    assert e.tecnico == {"type": "ZeroDivisionError", "message": "division by zero", "line": 3, "snippet": "x = 1 / 0"}


def test_excecao_dentro_de_um_item_da_lista_diz_qual_item():
    e = erro_da_sandbox({"category": "excecao", "type": "KeyError", "message": "'preco'", "item_index": 1})
    assert e.mensagem == "No item 2 da lista: o código procurou uma chave que não existe: 'preco'."
    assert e.tecnico == {"type": "KeyError", "message": "'preco'", "item_index": 1}


def test_excecao_de_tipo_desconhecido_cita_o_tipo():
    e = erro_da_sandbox({"category": "excecao", "type": "MeuErro", "message": "x"})
    assert e.mensagem == "O código gerou um erro do tipo MeuErro."


def test_sintaxe_tempo_esgotado_e_memoria_ganham_sugestao_propria():
    sintaxe = erro_da_sandbox({"category": "sintaxe", "message": "invalid syntax", "line": 2})
    assert sintaxe.mensagem == "Há um erro de escrita (sintaxe) no código (linha 2): invalid syntax"
    assert sintaxe.codigo == "sintaxe_python" and "parênteses" in (sintaxe.sugestao or "")

    tempo = erro_da_sandbox({"category": "tempo_esgotado", "message": "Passou de 4 s.", "line": 5, "snippet": "while True: pass"})
    assert tempo.mensagem == "Passou de 4 s (o código estava na linha 5: `while True: pass`)."
    assert tempo.codigo == "tempo_esgotado" and "while True" in (tempo.sugestao or "")

    memoria = erro_da_sandbox({"category": "memoria_excedida", "message": "Memória acabou", "line": 7})
    assert memoria.mensagem == "Memória acabou (linha 7)" and "listas ou textos enormes" in (memoria.sugestao or "")


def test_sugestao_vinda_do_executor_vence_a_padrao_e_categoria_desconhecida_vira_codigo_proprio():
    e = erro_da_sandbox({"category": "retorno_invalido", "message": "Faltou a saída.", "suggestion": "Inclua a saída."})
    assert (e.mensagem, e.codigo, e.sugestao) == ("Faltou a saída.", "retorno_invalido", "Inclua a saída.")
    outra = erro_da_sandbox({"category": "algo_novo", "message": "?"})
    assert (outra.mensagem, outra.codigo, outra.sugestao, outra.tecnico) == ("?", "algo_novo", None, {"message": "?"})
    assert erro_da_sandbox({}).mensagem == "Ocorreu um erro inesperado ao executar o código."


def test_erros_de_configuracao_nao_sao_repetidos_mas_os_de_execucao_sim():
    assert {"entrada_invalida", "entrada_ausente", "executor_indisponivel", "limite_itens", "registros_demais"} <= NAO_REPETIR
    assert not {"excecao_python", "tempo_esgotado", "erro_interno", "retorno_invalido"} & NAO_REPETIR


# ------------------------------------------------------------------ estado de uma execução
def test_chave_da_etapa_inclui_a_posicao_em_cada_laco():
    assert chave_etapa("passo", ()) == "passo"
    assert chave_etapa("passo", (3,)) == "passo@3"
    assert chave_etapa("passo", (0, 12)) == "passo@0.12"


def test_checar_cancelamento_so_interrompe_depois_do_pedido():
    ex = Execucao(run_id="r", flow=Flow(steps=[]), defs={}, port_types={}, trigger_inputs={}, cancelar=threading.Event())
    ex.checar_cancelamento()  # ainda não foi pedido
    ex.cancelar.set()
    with pytest.raises(Cancelado):
        ex.checar_cancelamento()


def test_falha_de_leva_a_linha_do_erro_tecnico_para_o_nivel_de_cima():
    erro = ErroBloco("Deu ruim.", codigo="excecao_python", sugestao="Revise.", tecnico={"line": 4, "type": "KeyError"})
    assert falha_de("p1", "Calcular", erro) == {
        "step_id": "p1", "step_name": "Calcular", "code": "excecao_python", "message": "Deu ruim.",
        "suggestion": "Revise.", "line": 4, "technical": {"line": 4, "type": "KeyError"}}
    sem_tecnico = falha_de("p2", "Outro", ErroBloco("Falhou."))
    assert sem_tecnico["line"] is None and sem_tecnico["technical"] is None and sem_tecnico["code"] == "erro_bloco"


# ------------------------------------------------------------------ histórico
ESCRITAS = ("criar_execucao", "atualizar_execucao", "garantir_etapa", "atualizar_etapa", "encerrar_etapas_abertas")


def _execucao_com_linha(store, etapas=("a",)):
    historico = Historico(store)
    rid = historico.criar_execucao(kind="fluxo", project_id=None, snapshot={}, etapas=list(etapas))
    ex = Execucao(run_id=rid, flow=Flow(steps=[]), defs={}, port_types={}, trigger_inputs={}, cancelar=threading.Event(),
                  posicao=store.contar_etapas(rid))
    return historico, ex


def test_gravar_etapa_so_cria_a_linha_da_repeticao_na_primeira_vez(sem_docker):
    historico, ex = _execucao_com_linha(sem_docker.store)
    historico.gravar_etapa(ex, "a", (), state="executando")  # a linha do passo já nasceu com a execução
    assert (ex.posicao, ex.registros) == (1, 0)
    historico.gravar_etapa(ex, "a", (0,), state="executando")
    historico.gravar_etapa(ex, "a", (0,), state="concluido", outputs={"x": 1})
    assert (ex.posicao, ex.registros) == (2, 1)
    linhas = sem_docker.store.obter_execucao(ex.run_id)["steps"]
    assert [(p["iteration"], p["position"], p["state"]) for p in linhas] == [([], 0, "executando"), ([0], 1, "concluido")]
    assert linhas[1]["outputs"] == {"x": 1}


def test_gravar_etapa_recusa_passar_do_limite_de_registros_da_execucao(sem_docker):
    historico, ex = _execucao_com_linha(sem_docker.store)
    ex.max_registros = 2
    historico.gravar_etapa(ex, "a", (0,))
    historico.gravar_etapa(ex, "a", (1,))
    with pytest.raises(ErroBloco) as exc:
        historico.gravar_etapa(ex, "a", (2,))
    assert exc.value.codigo == "registros_demais" and "limite de 2 registros" in exc.value.mensagem
    assert Execucao(run_id="r", flow=Flow(steps=[]), defs={}, port_types={}, trigger_inputs={},
                    cancelar=threading.Event()).max_registros == MAX_REGISTROS


def test_toda_escrita_do_historico_passa_pela_classe_historico(sem_docker, monkeypatch):
    """Se alguém gravar direto no banco (sem passar por Historico), este teste acusa: é o ponto onde entrarão os eventos."""
    chamadas: list[tuple[str, str]] = []

    def espionar(nome):
        original = getattr(sem_docker.store, nome)

        def espiao(*args, **kwargs):
            chamadas.append((nome, sys._getframe(1).f_globals["__name__"]))
            return original(*args, **kwargs)
        monkeypatch.setattr(sem_docker.store, nome, espiao)

    for nome in ESCRITAS:
        espionar(nome)
    laco = passo("laco", "builtin.para_cada", {"lista": ref("gatilho", "l")}, {"limite": 5}, slots={"corpo": [compor("x", lit(1))]})
    execucoes = [
        fluxo([laco, saida("s", "n", ref("laco", "quantidade"))], [campo("l", "lista", [1, 2])]),
        fluxo([passo("fim", "builtin.encerrar", {"mensagem": lit("parei")}, {"estado": "falha"}), compor("depois", lit(1))]),
    ]
    for f in execucoes:
        sem_docker.executar(f)
    sem_docker.motor.testar_bloco(sem_docker.registro.resolver("builtin.matematica", 1), {"operacao": "somar"}, {"a": 1, "b": 2})
    assert {nome for nome, _ in chamadas} == set(ESCRITAS)  # o roteiro exercita todos os tipos de escrita
    assert {quem for _, quem in chamadas} == {"app.historico"}
