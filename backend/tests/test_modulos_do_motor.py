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
from app.execucao import Cancelado, Execucao, chave_etapa, falha_de
from app.models import Flow

BACKEND = Path(__file__).resolve().parents[1]

# Camadas de baixo para cima; só engine.py (a fachada) pode ficar no topo.
MODULOS = ["erros_sandbox", "execucao", "engine"]


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
