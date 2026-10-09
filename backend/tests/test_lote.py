"""Lote de Python no executor isolado: as peças que não precisam do Docker (tetos de tempo e de bytes, configuração, rótulo da
imagem, leitura das linhas do runner e entrega em ordem).

O que o contêiner de verdade faz (módulo refeito a cada item, limites por item, cancelamento que abate) está em
``test_lote_docker.py``; o laço em lote no motor, em ``test_lote_motor.py``; a prova de que os dois caminhos deixam o mesmo
histórico, em ``test_lote_equivalencia.py``."""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.config import Limites, carregar_settings
from app.sandbox import ExecutorStatus, SandboxResult, corpo_max_do_lote, tempo_do_lote
from app.sandbox.executor import PREFIXO, _tem_rotulo_de_lote
from app.sandbox.lote import EntregaDoLote, LeitorDeLote


# ------------------------------------------------------------------ tetos do lote
@pytest.mark.parametrize("tempo_s,maximo,itens,esperado", [
    (10, 600, 1, 10),     # um item: o tempo de um item
    (10, 600, 5, 50),     # n × tempo de um item
    (10, 600, 50, 500),
    (10, 600, 100, 600),  # passou do máximo configurável: vale o máximo
    (10, 3, 5, 10),       # um máximo menor que um item não tira o tempo de um item
    (0.5, 600, 4, 2),
])
def test_tempo_do_lote_e_n_vezes_o_tempo_de_um_item_ate_o_maximo(tempo_s, maximo, itens, esperado):
    assert tempo_do_lote(Limites(tempo_s=tempo_s, lote_tempo_max_s=maximo), itens) == esperado


@pytest.mark.parametrize("memoria_mb,corpo_max,esperado", [
    (256, 8 * 1024 * 1024, 8 * 1024 * 1024),
    (128, 8 * 1024 * 1024, 4 * 1024 * 1024),   # a memória manda: 1/32 dela
    (64, 8 * 1024 * 1024, 2 * 1024 * 1024),
    (1024, 8 * 1024 * 1024, 8 * 1024 * 1024),  # e o teto configurado manda quando é menor
    (256, 1000, 1000),
])
def test_entradas_do_lote_cabem_em_uma_fracao_da_memoria_do_runner(memoria_mb, corpo_max, esperado):
    assert corpo_max_do_lote(Limites(memoria_mb=memoria_mb, lote_corpo_max=corpo_max)) == esperado


def test_tempo_total_do_lote_vem_de_trama_batch_timeout_s_e_valor_ruim_volta_ao_padrao(monkeypatch):
    padrao = Limites().lote_tempo_max_s
    for valor, esperado in (("45", 45.0), ("0.5", 0.5), ("0", padrao), ("-3", padrao), ("nan", padrao), ("inf", padrao),
                            ("abc", padrao), ("1e9", padrao), ("86400", 86400.0)):
        monkeypatch.setenv("TRAMA_BATCH_TIMEOUT_S", valor)
        assert carregar_settings().limites.lote_tempo_max_s == esperado, valor
    monkeypatch.delenv("TRAMA_BATCH_TIMEOUT_S")
    assert carregar_settings().limites.lote_tempo_max_s == padrao == 600.0


# ------------------------------------------------------------------ imagem do executor
@pytest.mark.parametrize("saida,esperado", [
    ('{"trama.runner.lote":"1"}', True),
    ('{"outro":"x","trama.runner.lote":"1"}', True),
    ("null", False),             # imagem sem nenhum rótulo (a anterior ao lote)
    ("{}", False),
    ('{"trama.runner.lote":"0"}', False),
    ('{"trama.runner.lote":1}', False),
    ('["trama.runner.lote"]', False),
    ("", False),
    ("isto não é JSON", False),
])
def test_imagem_so_roda_laco_em_lote_se_tiver_o_rotulo_do_runner_novo(saida, esperado):
    assert _tem_rotulo_de_lote(saida) is esperado


def test_status_sem_o_rotulo_nao_promete_lote():
    assert ExecutorStatus(True, "img").lote is False


# ------------------------------------------------------------------ leitura das linhas do runner
TOKEN = "abc123"


def linha(corpo: str, token: str = TOKEN) -> bytes:
    return f"\n{PREFIXO}{token}{corpo}\n".encode()


def leitor() -> LeitorDeLote:
    return LeitorDeLote(PREFIXO, TOKEN)


def test_leitor_tira_do_buffer_as_linhas_completas_na_ordem_em_que_chegaram():
    buffer = bytearray(linha('{"i": 0, "ok": true}') + linha('{"i": 1, "ok": false}') + linha('{"fim": true}'))
    objetos, ruido = leitor().ler(buffer)
    assert objetos == [{"i": 0, "ok": True}, {"i": 1, "ok": False}, {"fim": True}]
    assert ruido == 3  # só o "\n" que o runner escreve antes de cada linha
    assert len(buffer) < len(PREFIXO + TOKEN)  # sobra no máximo o que poderia ser o começo de um marcador


def test_leitor_espera_a_linha_terminar_e_nao_perde_o_que_veio_partido():
    um = leitor()
    todo = linha('{"i": 0, "ok": true, "logs": []}')
    buffer = bytearray()
    achados: list[dict[str, Any]] = []
    for pedaco in (todo[:4], todo[4:20], todo[20:-1], todo[-1:]):  # corta no meio do marcador, do JSON e antes do \n
        buffer.extend(pedaco)
        objetos, _ = um.ler(buffer)
        achados += objetos
    assert achados == [{"i": 0, "ok": True, "logs": []}]


def test_leitor_conta_como_ruido_o_que_nao_e_linha_do_runner_e_ignora_json_que_nao_e_objeto():
    buffer = bytearray(b"texto solto do codigo\n" + linha('{"i": 0}') + b"mais ruido" + linha("[1, 2]") + linha("{quebrado")
                       + linha('{"i": 1}', token="outro-token") + linha('{"i": 2}'))
    objetos, ruido = leitor().ler(buffer)
    assert objetos == [{"i": 0}, {"i": 2}]  # a linha do token errado, a que não é objeto e a ilegível não valem
    assert ruido > len(b"texto solto do codigo\nmais ruido")


def test_leitor_descarta_ruido_sem_marcador_mas_guarda_o_que_pode_ser_o_comeco_de_um_marcador():
    marcador = (PREFIXO + TOKEN).encode()
    sobra = len(marcador) - 1  # o que ainda poderia ser o começo de um marcador que chega partido
    buffer = bytearray(b"x" * 1000 + marcador[:5])
    objetos, ruido = leitor().ler(buffer)
    assert objetos == [] and ruido == 1005 - sobra and bytes(buffer) == (b"x" * 1000 + marcador[:5])[-sobra:]


# ------------------------------------------------------------------ entrega em ordem
def converter(linha_: dict[str, Any], ms: int) -> SandboxResult:
    return SandboxResult(bool(linha_.get("ok")), payload={"outputs": {"i": linha_.get("i")}}, duration_ms=ms)


def entrega(itens: int, parar_em: int | None = None):
    recebidos: list[tuple[int, bool]] = []

    def ao_item(posicao: int, resultado: SandboxResult) -> bool:
        recebidos.append((posicao, resultado.ok))
        return posicao != parar_em

    return EntregaDoLote(itens, ao_item, converter), recebidos


def test_entrega_manda_cada_item_na_ordem_e_termina_no_ultimo():
    e, recebidos = entrega(3)
    for i in range(3):
        assert e.aberto
        e.aceitar({"i": i, "ok": True})
    assert recebidos == [(0, True), (1, True), (2, True)] and not e.aberto and not e.falhou


def test_entrega_ignora_linha_repetida_adiantada_ou_de_item_que_nao_existe():
    e, recebidos = entrega(3)
    for lixo in ({"i": 1, "ok": True}, {"i": 5, "ok": True}, {"i": -1, "ok": True}, {"i": True, "ok": True},
                 {"i": "0", "ok": True}, {"i": 0.0, "ok": True}, {"i": None, "ok": True}, {"ok": True}, {}):
        e.aceitar(lixo)
    assert recebidos == [] and e.aberto  # nada disso é o resultado do item 0
    e.aceitar({"i": 0, "ok": True})
    e.aceitar({"i": 0, "ok": False})  # repetida: o primeiro vale
    assert recebidos == [(0, True)]


def test_entrega_para_na_primeira_falha_e_ignora_o_que_vier_depois():
    e, recebidos = entrega(4)
    e.aceitar({"i": 0, "ok": True})
    e.aceitar({"i": 1, "ok": False})
    e.aceitar({"i": 2, "ok": True})
    assert recebidos == [(0, True), (1, False)] and e.falhou and not e.aberto


def test_entrega_para_quando_quem_recebe_pede():
    e, recebidos = entrega(4, parar_em=1)
    for i in range(4):
        e.aceitar({"i": i, "ok": True})
    assert recebidos == [(0, True), (1, True)] and e.parado and not e.aberto and not e.falhou


def test_entrega_trata_falha_sem_posicao_como_a_do_item_em_andamento_mas_nao_um_ok_sem_posicao():
    e, recebidos = entrega(3)
    e.aceitar({"ok": True, "payload": {"outputs": {}}})  # a resposta de um runner antigo ao trabalho em lote: não vale
    assert recebidos == []
    e.aceitar({"i": 0, "ok": True})
    e.aceitar({"ok": False, "error": {"category": "erro_interno"}})
    assert recebidos == [(0, True), (1, False)] and e.falhou


def test_entrega_registra_o_fim_avisado_pelo_runner_e_conta_o_tempo_do_item_a_partir_da_ultima_entrega():
    e, _ = entrega(2)
    assert not e.fim
    e.aceitar({"fim": True})
    assert e.fim and e.aberto  # avisar que acabou não entrega nada
    antes = e.marco
    time.sleep(0.02)
    e.aceitar({"i": 0, "ok": True})
    assert e.marco > antes
