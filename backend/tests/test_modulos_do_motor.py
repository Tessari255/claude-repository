"""Módulos em que o motor foi dividido: tradução de erros, estado, histórico, conteúdo dinâmico e controle de fluxo.

O comportamento do motor inteiro está em ``test_engine.py``; aqui ficam as regras de cada peça isolada e as garantias
de arquitetura (quem importa quem, quem escreve o histórico)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest

from app.blocks.builtin import ContextoBloco
from app.config import Limites
from app.controle import Controle, deve_rodar, motivo_ignorado
from app.despacho import Despacho
from app.dinamico import avaliar_regras, montar_entradas, resolver_campo, valor_da_ref
from app.errors import ApiError, ErroBloco
from app.erros_sandbox import NAO_REPETIR, erro_da_sandbox
from app.execucao import MAX_REGISTROS, Cancelado, Encerrado, Execucao, Resultado, chave_etapa, falha_de
from app.historico import Historico
from app.models import Campo, Flow, Passo, Ref
from app.passos_simples import PassosSimples
from app.preparo import preparar_execucao
from app.sandbox import DockerExecutor, ExecutorStatus, SandboxResult

from .helpers import campo, compor, condicao, fluxo, lit, matematica, passo, python_inline, ref, saida, tpl

BACKEND = Path(__file__).resolve().parents[1]

# Camadas de baixo para cima; só engine.py (a fachada) pode ficar no topo.
MODULOS = ["erros_sandbox", "execucao", "historico", "dinamico", "passos_simples", "controle", "despacho", "preparo", "engine"]


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


# ------------------------------------------------------------------ conteúdo dinâmico e regras
def _execucao_com_valores(valores, nomes=None, port_types=None):
    return Execucao(run_id="r", flow=Flow(steps=[]), defs={}, port_types=port_types or {}, trigger_inputs={},
                    cancelar=threading.Event(), valores=valores, nomes=nomes or {})


def _campo(dados) -> Campo:
    return Campo.model_validate(dados)


def test_referencia_le_a_saida_e_segue_o_caminho_por_chaves_e_posicoes():
    ex = _execucao_com_valores({"a": {"dados": {"itens": [{"nome": "x"}, {"nome": "y"}]}}})
    assert valor_da_ref(ex, Ref(step="a", output="dados")) == {"itens": [{"nome": "x"}, {"nome": "y"}]}
    assert valor_da_ref(ex, Ref(step="a", output="dados", path="itens.1.nome")) == "y"


def test_referencia_a_passo_que_nao_rodou_ou_a_campo_inexistente_explica_o_que_faltou():
    ex = _execucao_com_valores({"a": {"dados": {"k": 1}}}, nomes={"a": "Buscar dados", "b": "Outro"})
    with pytest.raises(ErroBloco) as sem_passo:
        valor_da_ref(ex, Ref(step="b", output="dados"))
    assert sem_passo.value.codigo == "conteudo_indisponivel" and "Outro › dados" in sem_passo.value.mensagem
    with pytest.raises(ErroBloco) as sem_saida:
        valor_da_ref(ex, Ref(step="a", output="outra"))
    assert sem_saida.value.codigo == "conteudo_indisponivel" and "Buscar dados › outra" in sem_saida.value.mensagem
    with pytest.raises(ErroBloco) as sem_campo:
        valor_da_ref(ex, Ref(step="a", output="dados", path="k.z"))
    assert sem_campo.value.codigo == "campo_ausente" and "“k.z” não existe em “Buscar dados › dados”" in sem_campo.value.mensagem


def test_conteudo_dinamico_nunca_avalia_nada_so_le_chaves_e_posicoes():
    ex = _execucao_com_valores({"a": {"v": {"conta": "1 + 1", "lista": [1]}, "texto": "abc"}})
    assert resolver_campo(ex, _campo({"parts": ["= ", {"step": "a", "output": "v", "path": "conta"}]}), Limites()) == "= 1 + 1"
    for caminho in ("__class__", "lista.__len__", "conta.upper", "lista.0.real"):
        with pytest.raises(ErroBloco) as exc:
            valor_da_ref(ex, Ref(step="a", output="v", path=caminho))
        assert exc.value.codigo == "campo_ausente", caminho


def test_uma_unica_referencia_preserva_o_tipo_e_varias_partes_viram_texto():
    ex = _execucao_com_valores({"a": {"lista": [1, 2], "n": 7}})
    assert resolver_campo(ex, _campo(ref("a", "lista")), Limites()) == [1, 2]
    assert resolver_campo(ex, _campo(lit({"x": 1})), Limites()) == {"x": 1}
    assert resolver_campo(ex, _campo(tpl("Total: ", ("a", "n"), " itens")), Limites()) == "Total: 7 itens"


def test_texto_montado_acima_do_limite_de_tamanho_falha_antes_de_crescer():
    ex = _execucao_com_valores({"a": {"t": "x" * 2048}})
    with pytest.raises(ErroBloco) as exc:
        resolver_campo(ex, _campo(tpl(("a", "t"), ("a", "t"))), Limites(valor_max=3000))
    assert exc.value.codigo == "valor_grande_demais"


def test_montar_entradas_aplica_padrao_exige_obrigatorias_e_confere_o_tipo(sem_docker):
    matematica = sem_docker.registro.resolver("builtin.matematica", 1)
    incrementar = sem_docker.registro.resolver("builtin.var_incrementar", 1)
    ex = _execucao_com_valores({"g": {"n": 4, "t": "quatro"}})

    p = Passo(id="m", type=matematica.id, version=1, inputs={"a": _campo(ref("g", "n")), "b": _campo(lit(3))})
    assert montar_entradas(ex, p, matematica, Limites()) == {"a": 4, "b": 3}
    assert montar_entradas(ex, Passo(id="i", type=incrementar.id, version=1), incrementar, Limites()) == {"quantidade": 1}

    with pytest.raises(ErroBloco) as faltando:
        montar_entradas(ex, Passo(id="m", type=matematica.id, version=1, inputs={"a": _campo(lit(1))}), matematica, Limites())
    assert faltando.value.codigo == "entrada_ausente" and "“B”" in faltando.value.mensagem

    errado = Passo(id="m", type=matematica.id, version=1, inputs={"a": _campo(ref("g", "t")), "b": _campo(lit(3))})
    with pytest.raises(ErroBloco) as tipo:
        montar_entradas(ex, errado, matematica, Limites())
    assert tipo.value.codigo == "entrada_invalida" and "“A” esperava número" in tipo.value.mensagem


def _regras(*regras, combinador="e"):
    return {"combinador": combinador, "regras": list(regras)}


def test_regras_combinam_com_e_ou_e_comparam_conteudo_dinamico():
    ex = _execucao_com_valores({"g": {"n": 5}})
    maior = {"esq": ref("g", "n"), "op": "maior", "dir": lit(3)}
    menor = {"esq": ref("g", "n"), "op": "menor", "dir": lit(3)}
    assert avaliar_regras(ex, _regras(maior), Limites()) is True
    assert avaliar_regras(ex, _regras(maior, menor), Limites()) is False
    assert avaliar_regras(ex, _regras(maior, menor, combinador="ou"), Limites()) is True


def test_regras_mal_formadas_viram_erro_de_parametro():
    ex = _execucao_com_valores({})
    with pytest.raises(ErroBloco) as exc:
        avaliar_regras(ex, {"regras": []}, Limites())
    assert exc.value.codigo == "parametro_invalido" and "Adicione ao menos uma condição" in exc.value.mensagem


# ------------------------------------------------------------------ passos simples (executor isolado simulado)
class ExecutorFalso:
    """Faz o papel do executor isolado: devolve o que o teste mandar e guarda o que recebeu."""

    def __init__(self, resposta: SandboxResult | None = None, disponivel: bool = True) -> None:
        self.resposta = resposta or SandboxResult(ok=True, payload={"outputs": {"mensagem": "ok"}})
        self.disponivel = disponivel
        self.chamadas: list[dict] = []

    def status(self, forcar: bool = False) -> ExecutorStatus:
        return ExecutorStatus(disponivel=self.disponivel, imagem="falsa", motivo=None if self.disponivel else "docker_ausente",
                              mensagem=None if self.disponivel else "Sem Docker.", instrucao="Instale o Docker.")

    def run(self, mode, code, inputs=None, params=None, items=None, limits=None):
        self.chamadas.append({"mode": mode, "code": code, "inputs": inputs, "limits": limits})
        return self.resposta


def _rodar_python(sem_docker, executor, limites=None, **kw):
    """Executa um passo de código Python inline pelo PassosSimples e devolve as saídas."""
    limites = limites or Limites()
    p = Passo.model_validate(python_inline("p", "def run(inputs, params):\n    return {}", {"n": ("numero", lit(2))}, {"mensagem": "texto"}, **kw))
    historico, ex = _execucao_com_linha(sem_docker.store, etapas=("p",))
    ctx = ContextoBloco(limites=limites)
    simples = PassosSimples(historico, executor, limites)
    return simples.executar_folha(ex, p, sem_docker.registro.resolver("builtin.python", 1), (), ctx), ctx, ex


def test_resposta_valida_do_executor_vira_as_saidas_e_os_logs_do_passo(sem_docker):
    executor = ExecutorFalso(SandboxResult(ok=True, payload={"outputs": {"mensagem": "olá"}},
                                           logs=[{"source": "stdout", "text": "oi"}]))
    saidas, ctx, ex = _rodar_python(sem_docker, executor)
    assert saidas == {"mensagem": "olá"} and ex.valores["p"] == {"mensagem": "olá"}
    assert ctx.logs == [{"source": "stdout", "text": "oi"}]
    assert executor.chamadas[0]["mode"] == "block" and executor.chamadas[0]["inputs"] == {"n": 2}
    assert sem_docker.store.obter_execucao(ex.run_id)["steps"][0]["inputs"] == {"n": 2}  # as entradas ficam no histórico


@pytest.mark.parametrize("saidas,codigo,trecho", [
    ({}, "retorno_invalido", "não devolveu a saída declarada"),
    ({"mensagem": "x", "extra": 1}, "retorno_invalido", "extra"),
    ({"mensagem": 42}, "retorno_invalido", "deveria ser texto"),
    ({"mensagem": {1, 2}}, "retorno_invalido", "JSON"),
    ("texto solto", "retorno_invalido", "não é um dicionário"),
    ({"mensagem": "x" * 200}, "valor_grande_demais", "grande demais"),
])
def test_resposta_do_executor_nao_e_confiavel_e_e_conferida_contra_o_contrato(sem_docker, saidas, codigo, trecho):
    executor = ExecutorFalso(SandboxResult(ok=True, payload={"outputs": saidas}))
    with pytest.raises(ErroBloco) as exc:
        _rodar_python(sem_docker, executor, Limites(valor_max=100))
    assert exc.value.codigo == codigo and trecho in exc.value.mensagem


def test_erro_do_executor_chega_traduzido_e_o_passo_e_tentado_de_novo(sem_docker):
    executor = ExecutorFalso(SandboxResult(ok=False, error={"category": "excecao", "type": "ZeroDivisionError", "message": "x", "line": 2},
                                           logs=[{"source": "stderr", "text": "Traceback"}]))
    with pytest.raises(ErroBloco) as exc:
        _rodar_python(sem_docker, executor, retry=2)
    assert exc.value.codigo == "excecao_python" and "dividir por zero" in exc.value.mensagem
    assert len(executor.chamadas) == 3  # a primeira tentativa mais as 2 repetições pedidas


def test_sem_executor_disponivel_o_codigo_nao_roda_em_lugar_nenhum(sem_docker):
    executor = ExecutorFalso(disponivel=False)
    with pytest.raises(ErroBloco) as exc:
        _rodar_python(sem_docker, executor)
    assert exc.value.codigo == "executor_indisponivel" and exc.value.sugestao == "Instale o Docker."
    assert executor.chamadas == []


@pytest.mark.parametrize("timeout_do_passo,esperado", [(1.0, 1.0), (60.0, 5.0), (None, 5.0)])
def test_tempo_do_passo_so_pode_diminuir_o_limite_do_executor(sem_docker, timeout_do_passo, esperado):
    executor = ExecutorFalso()
    _rodar_python(sem_docker, executor, Limites(tempo_s=5.0), timeout=timeout_do_passo)
    assert executor.chamadas[0]["limits"].tempo_s == esperado


# ------------------------------------------------------------------ controle de fluxo (passos simulados)
def _passo_simples(id, run_after=None):
    return Passo.model_validate(passo(id, "builtin.compor", {"entrada": lit(1)}, run_after=run_after))


@pytest.mark.parametrize("run_after,anterior,roda,situacao", [
    (["sucesso"], Resultado("concluido"), True, "concluido"),
    (["sucesso"], Resultado("falhou"), False, "falhou"),
    (["sucesso"], Resultado("ignorado"), False, "ignorado"),
    (["falhou"], Resultado("falhou"), True, "falhou"),
    (["falhou"], Resultado("falhou", expirou=True), False, "expirou"),
    (["expirou"], Resultado("falhou", expirou=True), True, "expirou"),
    (["expirou"], Resultado("falhou"), False, "falhou"),
    (["ignorado"], Resultado("ignorado"), True, "ignorado"),
    (["sucesso", "falhou"], Resultado("falhou"), True, "falhou"),
    (["falhou", "expirou"], Resultado("concluido"), False, "concluido"),
])
def test_executar_apos_decide_pelo_que_aconteceu_com_o_passo_anterior(run_after, anterior, roda, situacao):
    assert deve_rodar(_passo_simples("p", run_after), anterior) == (roda, situacao)


def test_motivo_de_um_passo_ignorado_diz_o_que_aconteceu_e_o_que_ele_esperava():
    assert motivo_ignorado(_passo_simples("p"), "Buscar", "falhou") == (
        "Não executado: o passo anterior, “Buscar”, falhou, e este passo só roda se ele tiver sucesso.")
    assert motivo_ignorado(_passo_simples("p", ["falhou", "expirou"]), "Buscar", "concluido") == (
        "Não executado: o passo anterior, “Buscar”, teve sucesso, e este passo só roda se ele falhar ou expirar.")


def _controle(sem_docker, comportamento, etapas, limites=None):
    """Controle cujos passos são simulados: ``comportamento(ex, passo, iteracao)`` devolve o Resultado de cada um."""
    historico, ex = _execucao_com_linha(sem_docker.store, etapas)
    ex.nomes = {e: f"Passo {e.upper()}" for e in etapas}
    visto: list[tuple[str, tuple[int, ...]]] = []

    def executar(ex_, p, iteracao):
        visto.append((p.id, iteracao))
        return comportamento(ex_, p, iteracao)
    return Controle(historico, limites or Limites(), executar), ex, visto


def _falhou(passo_id="b", mensagem="deu ruim"):
    falha = {"step_id": passo_id, "step_name": f"Passo {passo_id.upper()}", "message": mensagem}
    return Resultado("falhou", False, [falha], mensagem)


def _linhas(sem_docker, ex):
    return {(s["step_id"], tuple(s["iteration"])): s for s in sem_docker.store.obter_execucao(ex.run_id)["steps"]}


def test_falha_sem_tratamento_ignora_o_proximo_passo_e_sobe_para_o_nivel_de_cima(sem_docker):
    controle, ex, visto = _controle(sem_docker, lambda ex_, p, it: _falhou() if p.id == "b" else Resultado("concluido"), ("a", "b", "c"))
    res = controle.lista(ex, [_passo_simples("a"), _passo_simples("b"), _passo_simples("c")], ())
    assert visto == [("a", ()), ("b", ())]
    assert res.falhas == _falhou().falhas
    assert res.resumo == [{"passo": "Passo A", "estado": "concluido", "erro": None},
                          {"passo": "Passo B", "estado": "falhou", "erro": "deu ruim"},
                          {"passo": "Passo C", "estado": "ignorado", "erro": None}]
    c = _linhas(sem_docker, ex)[("c", ())]
    assert c["state"] == "ignorado" and c["skip_reason"].endswith("falhou, e este passo só roda se ele tiver sucesso.")


def test_passo_que_roda_apos_a_falha_trata_o_erro_e_a_lista_termina_sem_falhas(sem_docker):
    controle, ex, visto = _controle(sem_docker, lambda ex_, p, it: _falhou() if p.id == "b" else Resultado("concluido"), ("a", "b", "c"))
    res = controle.lista(ex, [_passo_simples("a"), _passo_simples("b"), _passo_simples("c", ["falhou"])], ())
    assert visto == [("a", ()), ("b", ()), ("c", ())] and res.falhas == []


def test_ignorar_marca_tambem_os_passos_de_dentro_do_bloco(sem_docker):
    controle, ex, _ = _controle(sem_docker, lambda ex_, p, it: Resultado("concluido"), ("escopo",))
    escopo = Passo.model_validate(passo("escopo", "builtin.escopo", slots={"corpo": [compor("dentro", lit(1))]}))
    controle.ignorar(ex, escopo, (), "motivo qualquer")
    linhas = _linhas(sem_docker, ex)
    assert linhas[("escopo", ())]["skip_reason"] == "motivo qualquer"
    assert linhas[("dentro", ())]["state"] == "ignorado" and linhas[("dentro", ())]["skip_reason"] == "O bloco “Passo ESCOPO” não foi executado."


def test_condicao_roda_so_o_caminho_escolhido_e_ignora_o_outro(sem_docker):
    controle, ex, visto = _controle(sem_docker, lambda ex_, p, it: Resultado("concluido"), ("c", "s", "n"))
    cond = Passo.model_validate(condicao("c", lit(1), "igual", "1", sim=[compor("s", lit(1))], nao=[compor("n", lit(2))]))
    saidas, falhas, logs = controle.conteiner(ex, cond, sem_docker.registro.resolver("builtin.condicao", 1), ())
    assert (saidas, falhas) == ({"resultado": True}, []) and visto == [("s", ())]
    assert logs == [{"source": "system", "text": "Teste concluído: sim. Seguindo por “Se sim”."}]
    n = _linhas(sem_docker, ex)[("n", ())]
    assert n["state"] == "ignorado" and n["skip_reason"] == "O caminho “Se não” da condição “Passo C” não foi escolhido."


def test_para_cada_entrega_item_e_indice_e_para_na_primeira_falha(sem_docker):
    vistos: list[dict] = []

    def comportamento(ex_, p, it):
        vistos.append(dict(ex_.valores["laco"]))
        return _falhou("x") if it == (1,) else Resultado("concluido")
    controle, ex, visto = _controle(sem_docker, comportamento, ("laco",))
    laco = Passo.model_validate(passo("laco", "builtin.para_cada", {"lista": lit([10, 20, 30])}, {"limite": 5},
                                      slots={"corpo": [compor("x", lit(1))]}))
    saidas, falhas, logs = controle.conteiner(ex, laco, sem_docker.registro.resolver("builtin.para_cada", 1), ())
    assert visto == [("x", (0,)), ("x", (1,))]
    assert vistos == [{"item": 10, "indice": 0}, {"item": 20, "indice": 1}]
    assert saidas == {"quantidade": 3} and falhas == _falhou("x").falhas
    assert logs == [{"source": "system", "text": "O item 2 de 3 falhou; as repetições seguintes foram canceladas."}]


def test_para_cada_recusa_lista_acima_do_limite_sem_cortar_em_silencio(sem_docker):
    controle, ex, visto = _controle(sem_docker, lambda ex_, p, it: Resultado("concluido"), ("laco",))
    laco = Passo.model_validate(passo("laco", "builtin.para_cada", {"lista": lit([1, 2, 3])}, {"limite": 2},
                                      slots={"corpo": [compor("x", lit(1))]}))
    with pytest.raises(ErroBloco) as exc:
        controle.conteiner(ex, laco, sem_docker.registro.resolver("builtin.para_cada", 1), ())
    assert exc.value.codigo == "limite_itens" and visto == []


def _repetir_ate(limite):
    regra = {"esq": ref("v", "n"), "op": "maior_igual", "dir": lit(3)}
    return Passo.model_validate(passo("rep", "builtin.repetir_ate", params={"limite": limite, "combinador": "e", "regras": [regra]},
                                      slots={"corpo": [compor("x", lit(1))]}))


def _contar(ex_, p, it):
    ex_.valores["v"] = {"n": it[-1] + 1}
    return Resultado("concluido")


def test_repetir_ate_para_quando_a_condicao_fica_verdadeira(sem_docker):
    controle, ex, visto = _controle(sem_docker, _contar, ("rep",))
    saidas, falhas, _ = controle.conteiner(ex, _repetir_ate(5), sem_docker.registro.resolver("builtin.repetir_ate", 1), ())
    assert saidas == {"repeticoes": 3} and falhas == [] and visto == [("x", (0,)), ("x", (1,)), ("x", (2,))]


def test_repetir_ate_falha_quando_estoura_o_limite_de_repeticoes(sem_docker):
    controle, ex, visto = _controle(sem_docker, _contar, ("rep",))
    with pytest.raises(ErroBloco) as exc:
        controle.conteiner(ex, _repetir_ate(2), sem_docker.registro.resolver("builtin.repetir_ate", 1), ())
    assert exc.value.codigo == "limite_repeticoes" and len(visto) == 2


def test_escopo_resume_o_resultado_de_cada_passo_e_devolve_a_falha_ao_nivel_de_cima(sem_docker):
    controle, ex, _ = _controle(sem_docker, lambda ex_, p, it: _falhou("b") if p.id == "b" else Resultado("concluido"), ("tentar",))
    tentar = Passo.model_validate(passo("tentar", "builtin.escopo", slots={"corpo": [compor("a", lit(1)), compor("b", lit(1))]}))
    ex.nomes.update({"a": "Passo A", "b": "Passo B"})
    saidas, falhas, _ = controle.conteiner(ex, tentar, sem_docker.registro.resolver("builtin.escopo", 1), ())
    assert saidas == {"falhou": True, "erro": "deu ruim", "resultados": [
        {"passo": "Passo A", "estado": "concluido", "erro": None}, {"passo": "Passo B", "estado": "falhou", "erro": "deu ruim"}]}
    assert falhas == _falhou("b").falhas
    assert ex.valores["tentar"] == saidas  # o passo seguinte lê o resumo como conteúdo dinâmico


# ------------------------------------------------------------------ despacho de um passo
def _despacho(sem_docker, etapas):
    historico, ex = _execucao_com_linha(sem_docker.store, etapas)
    tipos = [sem_docker.registro.resolver(t, 1) for t in ("builtin.compor", "builtin.matematica", "builtin.escopo")]
    ex.defs = {f"{t.id}@{t.version}": t for t in tipos}
    ex.nomes = {e: f"Passo {e.upper()}" for e in etapas}
    return Despacho(historico, cast(DockerExecutor, ExecutorFalso()), Limites()), ex


def _levantar(excecao):
    def levantar(*args, **kwargs):
        raise excecao
    return levantar


def test_passo_concluido_passa_por_executando_e_grava_entradas_saidas_e_duracao(sem_docker):
    d, ex = _despacho(sem_docker, ("c",))
    assert d.passo(ex, Passo.model_validate(compor("c", lit(7))), ()) == Resultado("concluido")
    linha = _linhas(sem_docker, ex)[("c", ())]
    assert (linha["state"], linha["inputs"], linha["outputs"]) == ("concluido", {"entrada": 7}, {"resultado": 7})
    assert linha["started_at"] and linha["finished_at"] and linha["duration_ms"] >= 0


def test_falha_prevista_vira_resultado_falhou_com_o_erro_gravado_no_passo(sem_docker):
    d, ex = _despacho(sem_docker, ("m",))
    r = d.passo(ex, Passo.model_validate(matematica("m", lit(1), lit(0), "dividir")), ())
    assert r.estado == "falhou" and not r.expirou and r.mensagem == "Não é possível dividir por zero."
    assert r.falhas[0]["step_id"] == "m" and r.falhas[0]["step_name"] == "Passo M" and r.falhas[0]["code"] == "divisao_por_zero"
    linha = _linhas(sem_docker, ex)[("m", ())]
    assert linha["state"] == "falhou" and linha["error"]["code"] == "divisao_por_zero" and linha["outputs"] is None


def test_tempo_esgotado_marca_o_resultado_como_expirou_para_o_executar_apos(sem_docker):
    d, ex = _despacho(sem_docker, ("c",))
    d.simples.executar_folha = _levantar(ErroBloco("Passou do tempo.", codigo="tempo_esgotado"))
    r = d.passo(ex, Passo.model_validate(compor("c", lit(1))), ())
    assert r.estado == "falhou" and r.expirou is True


def test_erro_inesperado_nao_vaza_a_mensagem_interna_para_o_historico(sem_docker, caplog):
    d, ex = _despacho(sem_docker, ("c",))
    d.simples.executar_folha = _levantar(RuntimeError("segredo do servidor"))
    r = d.passo(ex, Passo.model_validate(compor("c", lit(1))), ())
    assert r.estado == "falhou" and r.falhas[0]["code"] == "erro_interno" and r.mensagem == "Ocorreu um erro interno ao executar este passo."
    linha = _linhas(sem_docker, ex)[("c", ())]
    assert linha["error"] == {"code": "erro_interno", "message": r.mensagem, "suggestion": None, "technical": {"type": "RuntimeError"}}
    assert "segredo do servidor" not in str(linha) and "segredo do servidor" in caplog.text  # só o log do servidor guarda o detalhe


@pytest.mark.parametrize("interrupcao,estado", [
    (Cancelado(), "cancelado"),
    (Encerrado("cancelado", "parei", Passo.model_validate(compor("c", lit(1)))), "cancelado"),
    (Encerrado("sucesso", "terminei", Passo.model_validate(compor("c", lit(1)))), "concluido"),
    (Encerrado("falha", "quebrei", Passo.model_validate(compor("c", lit(1)))), "concluido"),
])
def test_cancelar_e_encerrar_fecham_a_linha_do_passo_e_seguem_subindo(sem_docker, interrupcao, estado):
    d, ex = _despacho(sem_docker, ("c",))
    d.simples.executar_folha = _levantar(interrupcao)
    with pytest.raises(type(interrupcao)):
        d.passo(ex, Passo.model_validate(compor("c", lit(1))), ())
    assert _linhas(sem_docker, ex)[("c", ())]["state"] == estado


def test_bloco_com_passo_de_dentro_que_falhou_aponta_o_passo_e_guarda_o_resumo(sem_docker):
    d, ex = _despacho(sem_docker, ("tentar",))
    ex.nomes["div"] = "Dividir"
    tentar = Passo.model_validate(passo("tentar", "builtin.escopo", slots={"corpo": [matematica("div", lit(1), lit(0), "dividir")]}))
    r = d.passo(ex, tentar, ())
    assert r.estado == "falhou" and r.mensagem == "Não é possível dividir por zero." and r.falhas[0]["step_id"] == "div"
    linhas = _linhas(sem_docker, ex)
    assert linhas[("tentar", ())]["error"]["code"] == "falha_em_passo_interno"
    assert linhas[("tentar", ())]["error"]["message"] == "O passo “Dividir” dentro deste bloco falhou: Não é possível dividir por zero."
    assert linhas[("tentar", ())]["outputs"]["falhou"] is True and linhas[("div", ())]["state"] == "falhou"


# ------------------------------------------------------------------ preparo da execução
def _preparar(sem_docker, f, dados=None):
    return preparar_execucao(Flow.model_validate(f), None, dados, registro=sem_docker.registro, executor=sem_docker.executor,
                             limites=Limites(), historico=Historico(sem_docker.store))


def test_preparo_congela_o_fluxo_e_as_definicoes_e_cria_so_as_linhas_fora_de_laco(sem_docker):
    laco = passo("laco", "builtin.para_cada", {"lista": lit([1])}, {"limite": 5}, slots={"corpo": [compor("dentro", lit(1))]})
    f = fluxo([laco, condicao("c", lit(1), "igual", "1", sim=[compor("ramo", lit(1))])], [campo("n", "numero", 3)])
    run = sem_docker.store.obter_execucao(_preparar(sem_docker, f, {"n": 9}), com_snapshot=True)
    assert run["state"] == "aguardando" and run["trigger_inputs"] == {"n": 9}
    assert [s["step_id"] for s in run["steps"]] == ["gatilho", "laco", "c", "ramo"]  # "dentro" nasce a cada repetição
    snap = run["snapshot"]
    assert snap["flow"] == Flow.model_validate(f).model_dump() and snap["trigger_inputs"] == {"n": 9}
    assert {"builtin.gatilho_manual@1", "builtin.para_cada@1", "builtin.condicao@1", "builtin.compor@1"} <= set(snap["definitions"])
    assert "laco" in snap["port_types"]


def test_preparo_recusa_fluxo_invalido_sem_criar_execucao_nenhuma(sem_docker, settings):
    invalido = fluxo([passo("m", "builtin.matematica", {"a": lit(1)}, {"operacao": "somar"})])  # falta a entrada B
    with pytest.raises(ApiError) as exc:
        _preparar(sem_docker, invalido)
    assert exc.value.status == 422 and exc.value.codigo == "fluxo_invalido" and exc.value.problemas
    with closing(sqlite3.connect(settings.db_path)) as c:
        assert c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_dados_do_gatilho_com_varios_problemas_sao_apontados_de_uma_vez(sem_docker):
    f = fluxo([compor("c", lit(1))], [campo("nome", "texto", required=True), campo("idade", "numero", 18)])
    with pytest.raises(ApiError) as exc:
        _preparar(sem_docker, f, {"idade": "vinte", "extra": 1})
    mensagens = sorted(p["message"] for p in exc.value.problemas)
    assert exc.value.codigo == "dados_invalidos" and len(mensagens) == 3
    assert any("não tem o campo “extra”" in m for m in mensagens)
    assert any("“Idade” deveria ser número" in m for m in mensagens)
    assert any(m == "Preencha o campo “Nome” do gatilho." for m in mensagens)
