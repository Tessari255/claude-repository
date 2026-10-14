"""Lote no contêiner de verdade: o runner roda a função ``run`` uma vez por item, no mesmo processo, e o executor entrega um
resultado por item. Prova o que vale POR ITEM (tempo, memória, logs, tamanho), o que vaza entre itens, o teto do lote, o
cancelamento que abate o contêiner e que o isolamento é o mesmo do trabalho avulso."""

from __future__ import annotations

import dataclasses
import subprocess
import threading
import time

import pytest

from app.config import Limites
from app.sandbox import DockerExecutor, ExecutorStatus, SandboxResult
from app.sandbox.executor import ROTULO

from .conftest import IMAGEM, LIMITES_TESTE

pytestmark = pytest.mark.docker


def lote(executor, codigo, itens, params=None, parar_em=None, cancelar=None, **limites):
    """Roda o lote e devolve (desfecho, [(posição, resultado)]). ``parar_em``: posição em que o ``ao_item`` manda parar."""
    recebidos: list[tuple[int, SandboxResult]] = []

    def ao_item(posicao: int, resultado: SandboxResult) -> bool:
        recebidos.append((posicao, resultado))
        return posicao != parar_em

    lim = dataclasses.replace(LIMITES_TESTE, **limites) if limites else None
    desfecho = executor.run_lote(codigo, itens, params, lim, cancelar=cancelar, ao_item=ao_item)
    return desfecho, recebidos


def saidas(recebidos):
    return [r.payload["outputs"] if r.ok else None for _, r in recebidos]


def categorias(recebidos):
    return [None if r.ok else r.error["category"] for _, r in recebidos]


def conteineres_de_trabalho() -> list[str]:
    return subprocess.run(["docker", "ps", "-aq", "--filter", f"label={ROTULO}"], capture_output=True, text=True).stdout.split()


@pytest.fixture(autouse=True)
def sem_sobras(executor):
    """Nenhum teste deste módulo pode deixar contêiner para trás (vivo ou parado)."""
    antes = set(conteineres_de_trabalho())
    yield
    fim = time.monotonic() + 10
    while set(conteineres_de_trabalho()) - antes and time.monotonic() < fim:
        time.sleep(0.1)
    assert set(conteineres_de_trabalho()) - antes == set()


def test_a_imagem_construida_avisa_que_roda_lote(executor):
    assert executor.status(forcar=True).lote is True


def test_um_resultado_por_item_na_ordem_com_os_logs_de_cada_um_e_tudo_num_contêiner_so(executor):
    codigo = ("def run(inputs, params):\n    print('item', inputs['n'])\n"
              "    return {'dobro': inputs['n'] * 2, 'host': open('/etc/hostname').read().strip(), 'p': params['k']}\n")
    desfecho, recebidos = lote(executor, codigo, [{"n": n} for n in range(6)], {"k": "x"})
    assert desfecho == "concluido" and [p for p, _ in recebidos] == list(range(6))
    assert [s["dobro"] for s in saidas(recebidos)] == [0, 2, 4, 6, 8, 10] and {s["p"] for s in saidas(recebidos)} == {"x"}
    assert len({s["host"] for s in saidas(recebidos)}) == 1  # o mesmo contêiner: o lote é UM trabalho
    assert [r.logs for _, r in recebidos] == [[{"source": "stdout", "text": f"item {n}\n"}] for n in range(6)]  # logs de cada item, só dele


def test_o_resultado_de_cada_item_e_o_que_o_trabalho_avulso_daria(executor):
    codigo = "def run(inputs, params):\n    print('oi', inputs['t'])\n    return {'t': inputs['t'].upper(), 'n': len(inputs['t'])}\n"
    itens = [{"t": "ação"}, {"t": "日本語"}, {"t": "𝒳 emoji 😀"}, {"t": "a\u2028b\u0085c"}, {"t": ""}]
    _, recebidos = lote(executor, codigo, itens)
    for (_, em_lote), item in zip(recebidos, itens, strict=True):
        avulso = executor.run("block", codigo, item)
        assert (em_lote.ok, em_lote.payload, em_lote.logs, em_lote.error) == (avulso.ok, avulso.payload, avulso.logs, avulso.error)


# ------------------------------------------------------------------ o que é refeito e o que vaza entre itens
def test_modulo_do_usuario_e_executado_do_zero_a_cada_item(executor):
    codigo = ("contador = 0\nvistos = []\n\n"
              "def run(inputs, params):\n    global contador\n    contador += 1\n    vistos.append(inputs['n'])\n"
              "    novo = 'definido_no_item_%d' % inputs['n']\n    globals()[novo] = True\n"
              "    return {'contador': contador, 'vistos': list(vistos), 'sobras': [k for k in globals() if k.startswith('definido_')]}\n")
    _, recebidos = lote(executor, codigo, [{"n": n} for n in range(4)])
    assert [s["contador"] for s in saidas(recebidos)] == [1, 1, 1, 1]  # nenhum item enxerga o contador do anterior
    assert [s["vistos"] for s in saidas(recebidos)] == [[0], [1], [2], [3]]
    assert [s["sobras"] for s in saidas(recebidos)] == [[f"definido_no_item_{n}"] for n in range(4)]  # cada item só vê o que ele mesmo definiu


def test_params_chega_novo_a_cada_item_e_o_que_um_item_grava_em_tmp_nao_chega_ao_proximo(executor):
    codigo = ("def run(inputs, params):\n    mutado = 'extra' in params\n    params['extra'] = inputs['n']\n    params['lista'].append(inputs['n'])\n"
              "    try:\n        com_arquivo = open('/tmp/do_item_anterior').read()\n    except OSError:\n        com_arquivo = None\n"
              "    open('/tmp/do_item_anterior', 'w').write(str(inputs['n']))\n"
              "    return {'mutado': mutado, 'lista': list(params['lista']), 'arquivo': com_arquivo}\n")
    _, recebidos = lote(executor, codigo, [{"n": n} for n in range(4)], {"lista": []})
    assert [s["mutado"] for s in saidas(recebidos)] == [False] * 4  # cópia nova de params por item
    assert [s["lista"] for s in saidas(recebidos)] == [[n] for n in range(4)]
    assert [s["arquivo"] for s in saidas(recebidos)] == [None] * 4  # /tmp esvaziado entre os itens, como num contêiner novo


def test_estado_global_da_biblioteca_padrao_vaza_entre_itens_como_documentado(executor):
    """O módulo do usuário é refeito a cada item, mas o processo é o mesmo: o que o código muda nos módulos da biblioteca padrão
    (aqui, um atributo do `math`) continua valendo no item seguinte. Está dito no runner e no README; mudar isto exige mudar a documentação."""
    codigo = ("import math\n"
              "def run(inputs, params):\n    antes = getattr(math, 'marca_do_item', None)\n    math.marca_do_item = inputs['n']\n"
              "    return {'antes': antes}\n")
    _, recebidos = lote(executor, codigo, [{"n": n} for n in range(3)])
    assert [s["antes"] for s in saidas(recebidos)] == [None, 0, 1]
    _, avulsos = lote(executor, codigo, [{"n": 7}])  # em outro lote (outro contêiner) nada disso existe
    assert saidas(avulsos) == [{"antes": None}]


def test_memoria_de_um_item_e_devolvida_antes_do_proximo(executor):
    # a lista que aponta para si mesma só é liberada pelo coletor de ciclos: sem ele rodar entre os itens, 8 × 60 MB passaria dos 128 MB
    codigo = ("def run(inputs, params):\n    x = bytearray(60 * 1024 * 1024)\n    for i in range(0, len(x), 4096):\n        x[i] = 1\n"
              "    ciclo = [x]\n    ciclo.append(ciclo)\n    return {'ok': True}\n")
    desfecho, recebidos = lote(executor, codigo, [{}] * 8, memoria_mb=128)
    assert desfecho == "concluido" and categorias(recebidos) == [None] * 8


# ------------------------------------------------------------------ falhas: a primeira encerra o lote
def test_primeira_falha_encerra_o_lote_e_os_itens_seguintes_nem_rodam(executor):
    codigo = ("import time\ndef run(inputs, params):\n    if inputs['n'] == 2:\n        return {'v': 1 / 0}\n"
              "    if inputs['n'] > 2:\n        time.sleep(3)\n    return {'v': inputs['n']}\n")
    inicio = time.monotonic()
    desfecho, recebidos = lote(executor, codigo, [{"n": n} for n in range(6)])
    assert desfecho == "concluido" and [p for p, _ in recebidos] == [0, 1, 2]
    erro = recebidos[2][1].error
    assert erro["category"] == "excecao" and erro["type"] == "ZeroDivisionError" and erro["line"] == 4 and "1 / 0" in erro["snippet"]
    assert time.monotonic() - inicio < 2.5  # os itens 3, 4 e 5 (3 s cada) não chegaram a rodar


def test_erro_de_sintaxe_e_falta_da_funcao_run_sao_do_primeiro_item(executor):
    _, sintaxe = lote(executor, "def run(inputs, params)\n    return {}\n", [{}, {}])
    assert [p for p, _ in sintaxe] == [0] and sintaxe[0][1].error["category"] == "sintaxe" and sintaxe[0][1].error["line"] == 1
    _, ausente = lote(executor, "x = 1\n", [{}, {}])
    assert [p for p, _ in ausente] == [0] and ausente[0][1].error["category"] == "funcao_ausente"


def test_retorno_invalido_de_um_item_e_a_falha_dele(executor):
    codigo = "def run(inputs, params):\n    return {'a': {1, 2}} if inputs['n'] == 1 else {'a': 1}\n"
    _, recebidos = lote(executor, codigo, [{"n": 0}, {"n": 1}, {"n": 2}])
    assert categorias(recebidos) == [None, "retorno_invalido"] and "saídas['a']" in recebidos[1][1].error["message"]
    _, nao_dict = lote(executor, "def run(inputs, params):\n    return [1]\n", [{}, {}])
    assert categorias(nao_dict) == ["retorno_invalido"]


# ------------------------------------------------------------------ limites por item e tetos do lote
def test_tempo_esgotado_e_do_item_que_estourou_e_os_anteriores_chegaram(executor):
    codigo = "def run(inputs, params):\n    if inputs['n'] == 2:\n        while True:\n            pass\n    return {'v': inputs['n']}\n"
    inicio = time.monotonic()
    desfecho, recebidos = lote(executor, codigo, [{"n": n} for n in range(5)], tempo_s=1.5)
    assert desfecho == "concluido" and categorias(recebidos) == [None, None, "tempo_esgotado"]
    erro = recebidos[2][1].error
    assert erro["message"] == "O tempo máximo de 1.5 s foi excedido." and erro["line"] == 3 and erro["type"] != "TempoDoLote"
    assert time.monotonic() - inicio < 6


def test_cada_item_tem_o_tempo_inteiro_e_o_lote_so_para_no_teto_total(executor):
    codigo = "import time\ndef run(inputs, params):\n    time.sleep(0.7)\n    return {'v': inputs['n']}\n"
    desfecho, recebidos = lote(executor, codigo, [{"n": n} for n in range(4)], tempo_s=1.0)  # 4 × 0,7 s: cada um cabe, somados passam de 1 s
    assert desfecho == "concluido" and categorias(recebidos) == [None] * 4  # o total é 4 × 1 s, então nada estoura
    inicio = time.monotonic()
    desfecho, recebidos = lote(executor, codigo, [{"n": n} for n in range(8)], tempo_s=1.0, lote_tempo_max_s=2.0)
    assert desfecho == "concluido" and categorias(recebidos)[:2] == [None, None]
    ultimo = recebidos[-1][1]
    assert not ultimo.ok and ultimo.error["category"] == "tempo_esgotado" and ultimo.error["type"] == "TempoDoLote"
    assert "tempo total do laço (2 s" in ultimo.error["message"] and len(recebidos) < 8
    assert time.monotonic() - inicio < 5  # o runner corta no teto do lote; o host só abateria 5 s de folga depois dele


def test_memoria_estourada_num_item_e_dele_e_os_anteriores_chegaram(executor):
    codigo = "def run(inputs, params):\n    if inputs['n'] == 1:\n        x = bytearray(400 * 1024 * 1024)\n    return {'v': inputs['n']}\n"
    _, recebidos = lote(executor, codigo, [{"n": n} for n in range(3)], memoria_mb=64)
    assert categorias(recebidos) == [None, "memoria_excedida"] and recebidos[1][1].error["line"] == 3


def test_memoria_estourada_pelo_cgroup_derruba_o_contêiner_mas_os_itens_anteriores_ja_foram_entregues(executor):
    codigo = ("def run(inputs, params):\n    if inputs['n'] == 2:\n        x = bytearray(600 * 1024 * 1024)\n"
              "        for i in range(0, len(x), 4096):\n            x[i] = 1\n    return {'v': inputs['n']}\n")
    desfecho, recebidos = lote(executor, codigo, [{"n": n} for n in range(4)], memoria_mb=64, rlimit_as=False, tempo_s=8)
    assert desfecho == "concluido" and categorias(recebidos) == [None, None, "memoria_excedida"]


def test_logs_tem_teto_por_item_e_nao_pelo_lote_inteiro(executor):
    codigo = "def run(inputs, params):\n    print('x' * 3000)\n    return {'v': inputs['n']}\n"
    _, recebidos = lote(executor, codigo, [{"n": n} for n in range(10)], logs_max=5000)  # 10 × 3 KB passa dos 5 KB, mas cada item cabe
    assert categorias(recebidos) == [None] * 10
    flood = "def run(inputs, params):\n    if inputs['n'] == 1:\n        while True:\n            print('x' * 1000)\n    return {}\n"
    _, recebidos = lote(executor, flood, [{"n": n} for n in range(3)], logs_max=20_000)
    assert categorias(recebidos) == [None, "saida_excessiva"] and sum(len(p["text"]) for p in recebidos[1][1].logs) <= 20_000


def test_resultado_grande_demais_e_do_item_que_o_devolveu(executor):
    codigo = "def run(inputs, params):\n    return {'t': 'x' * inputs['tamanho']}\n"
    _, recebidos = lote(executor, codigo, [{"tamanho": 10}, {"tamanho": 5000}, {"tamanho": 10}], valor_max=2048)
    assert categorias(recebidos) == [None, "retorno_invalido"] and "grande demais" in recebidos[1][1].error["message"]


def test_volume_escrito_direto_no_descritor_e_abatido_e_atribuido_ao_item_em_andamento(executor):
    codigo = ("def run(inputs, params):\n    if inputs['n'] == 1:\n        f = open(1, 'w', closefd=False)\n"
              "        while True:\n            f.write('x' * 65536)\n            f.flush()\n    return {'v': inputs['n']}\n")
    desfecho, recebidos = lote(executor, codigo, [{"n": n} for n in range(3)], valor_max=64 * 1024, logs_max=16 * 1024, tempo_s=8)
    assert desfecho == "concluido" and categorias(recebidos) == [None, "saida_excessiva"]


def test_item_preso_numa_chamada_que_nao_ouve_o_aviso_e_abatido_pelo_host_no_tempo_de_um_item(executor):
    """O aviso de tempo só é tratado entre instruções do Python: uma conta longa em C não o ouve. Quem corta é o host, contando
    o tempo DO ITEM a partir da entrega do anterior (e não o do lote inteiro, que aqui é de 3 s)."""
    codigo = "def run(inputs, params):\n    if inputs['n'] == 1:\n        sum(range(10 ** 13))\n    return {'v': inputs['n']}\n"
    inicio = time.monotonic()
    desfecho, recebidos = lote(executor, codigo, [{"n": n} for n in range(3)], tempo_s=1, folga_inicio_s=3)
    assert desfecho == "concluido" and categorias(recebidos) == [None, "tempo_esgotado"]
    assert recebidos[1][1].error["message"] == "O tempo máximo de 1 s foi excedido e a execução foi encerrada."
    assert 3.5 < time.monotonic() - inicio < 10


def test_entrada_grande_demais_para_um_trabalho_falha_no_item_dela_e_os_anteriores_rodam(executor):
    codigo = "def run(inputs, params):\n    return {'n': len(inputs['t'])}\n"
    itens = [{"t": "a"}, {"t": "b" * 100}, {"t": "\x01" * 3000}, {"t": "c"}]  # o terceiro, em ASCII escapado (\u0001), passa de 4 × valor_max
    desfecho, recebidos = lote(executor, codigo, itens, valor_max=2048)
    assert desfecho == "concluido" and categorias(recebidos) == [None, None, "valor_grande_demais"]
    assert recebidos[2][1].error["message"] == executor.run("block", codigo, itens[2], limits=dataclasses.replace(LIMITES_TESTE, valor_max=2048)).error["message"]


def test_entradas_que_juntas_passam_do_teto_do_lote_falham_no_item_que_o_estourou(executor):
    codigo = "def run(inputs, params):\n    return {'n': len(inputs['t'])}\n"
    itens = [{"t": "a" * 100} for _ in range(10)]  # cada um tem ~115 bytes de JSON; o teto de 400 bytes cabe 3
    desfecho, recebidos = lote(executor, codigo, itens, lote_corpo_max=400)
    assert desfecho == "concluido" and categorias(recebidos) == [None, None, None, "valor_grande_demais"]
    assert "juntas" in recebidos[3][1].error["message"]


def test_lote_sem_itens_nao_sobe_contêiner(executor):
    desfecho, recebidos = lote(executor, "def run(inputs, params):\n    return {}\n", [])
    assert desfecho == "concluido" and recebidos == []


# ------------------------------------------------------------------ parar e cancelar abatem o contêiner
LENTO = "import time\ndef run(inputs, params):\n    if inputs['n'] >= 1:\n        time.sleep(60)\n    return {'v': inputs['n']}\n"


def test_cancelar_durante_o_lote_abate_o_contêiner_na_hora_e_os_itens_prontos_foram_entregues(executor):
    cancelar = threading.Event()
    primeiro_entregue = threading.Event()
    recebidos: list[tuple[int, SandboxResult]] = []
    resultado: dict = {}

    def ao_item(posicao: int, r: SandboxResult) -> bool:
        recebidos.append((posicao, r))
        primeiro_entregue.set()
        return True

    def rodar():
        lim = dataclasses.replace(LIMITES_TESTE, tempo_s=120, lote_tempo_max_s=300)
        resultado["desfecho"] = executor.run_lote(LENTO, [{"n": n} for n in range(4)], None, lim, cancelar=cancelar, ao_item=ao_item)

    fio = threading.Thread(target=rodar)
    fio.start()
    assert primeiro_entregue.wait(30)  # o item 0 terminou: agora o item 1 começa a dormir (60 s)
    time.sleep(0.5)
    inicio = time.monotonic()
    cancelar.set()
    fio.join(10)
    assert not fio.is_alive() and time.monotonic() - inicio < 5  # sem esperar os 60 s do item em andamento
    assert resultado["desfecho"] == "cancelado" and [p for p, _ in recebidos] == [0] and recebidos[0][1].ok  # o item em andamento não ganha resultado
    assert conteineres_de_trabalho() == []  # o contêiner morreu e foi removido


def test_cancelamento_pedido_antes_de_comecar_nao_roda_nada(executor):
    cancelar = threading.Event()
    cancelar.set()
    desfecho, recebidos = lote(executor, LENTO, [{"n": n} for n in range(3)], cancelar=cancelar, tempo_s=120, lote_tempo_max_s=300)
    assert desfecho == "cancelado" and recebidos == []


def test_quem_recebe_pode_mandar_parar_e_o_contêiner_e_abatido(executor):
    inicio = time.monotonic()
    desfecho, recebidos = lote(executor, LENTO, [{"n": n} for n in range(4)], parar_em=0, tempo_s=120, lote_tempo_max_s=300)
    assert desfecho == "parado" and [p for p, _ in recebidos] == [0]
    assert time.monotonic() - inicio < 6 and conteineres_de_trabalho() == []


def test_excecao_de_quem_recebe_abate_o_contêiner_e_sobe(executor):
    def ao_item(posicao, resultado):
        raise RuntimeError("falha de quem recebe")

    with pytest.raises(RuntimeError, match="falha de quem recebe"):
        executor.run_lote(LENTO, [{"n": 0}, {"n": 1}], None, dataclasses.replace(LIMITES_TESTE, tempo_s=120, lote_tempo_max_s=300), ao_item=ao_item)
    assert conteineres_de_trabalho() == []


# ------------------------------------------------------------------ o isolamento é o mesmo do trabalho avulso
def test_lote_roda_com_o_mesmo_isolamento_de_um_trabalho_avulso(executor):
    codigo = ("def run(inputs, params):\n"
              "    campos = {}\n    for linha in open('/proc/self/status'):\n        chave, _, valor = linha.partition(':')\n        campos[chave] = valor.strip()\n"
              "    interfaces = [l.split(':')[0].strip() for l in open('/proc/net/dev').read().splitlines()[2:]]\n"
              "    try:\n        open('/opt/trama/runner.py', 'a')\n        gravou = True\n    except OSError:\n        gravou = False\n"
              "    return {'uid': campos['Uid'].split()[0], 'caps': campos['CapEff'], 'novos_privilegios': campos['NoNewPrivs'],\n"
              "            'gravou_no_runner': gravou, 'interfaces': interfaces}\n")
    _, recebidos = lote(executor, codigo, [{}, {}, {}])
    assert len(recebidos) == 3
    for s in saidas(recebidos):
        assert s == {"uid": "65534", "caps": "0000000000000000", "novos_privilegios": "1", "gravou_no_runner": False, "interfaces": ["lo"]}


def test_comando_do_lote_so_difere_do_avulso_no_tempo_do_contêiner(executor):
    avulso = executor._comando("n", LIMITES_TESTE)
    do_lote = executor._comando("n", dataclasses.replace(LIMITES_TESTE, tempo_s=100.0))
    diferencas = [(a, b) for a, b in zip(avulso, do_lote, strict=True) if a != b]
    assert len(avulso) == len(do_lote)
    assert diferencas == [("cpu=7:8", "cpu=103:104"), ("14", "110")]  # limite de CPU e vigia: crescem com o tempo do lote; o resto é igual
    texto = " ".join(do_lote)
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", "--user 65534:65534",
                 "--ipc none", "--pids-limit 64", f"--label {ROTULO}"):
        assert flag in texto, flag
    assert "--rm" not in texto and " -v " not in texto and "--volume" not in texto


def test_lote_nunca_usa_o_pool_e_cada_lote_tem_contêiner_novo(executor_pool):
    ociosos = set(subprocess.run(["docker", "ps", "-q", "--filter", "label=trama.executor=pool"], capture_output=True, text=True).stdout.split())
    assert len(ociosos) == 2
    codigo = "def run(inputs, params):\n    return {'h': open('/etc/hostname').read().strip()}\n"
    usados = [saidas(lote(executor_pool, codigo, [{}, {}])[1])[0]["h"] for _ in range(2)]
    assert len(set(usados)) == 2 and not set(usados) & ociosos  # contêiner novo a cada lote, e nenhum ocioso foi usado
    assert executor_pool.estado_pool()["acertos"] == 0


def test_executor_sem_docker_responde_ao_primeiro_item_que_nao_ha_executor():
    ex = DockerExecutor(IMAGEM, Limites(), docker_bin="docker-que-nao-existe-xyz")
    desfecho, recebidos = lote(ex, "def run(inputs, params):\n    return {}\n", [{}, {}])
    assert desfecho == "concluido" and [p for p, _ in recebidos] == [0] and recebidos[0][1].error["category"] == "executor_indisponivel"


def test_imagem_antiga_sem_o_rotulo_responde_que_nao_roda_lote_em_vez_de_mandar_o_trabalho(executor, monkeypatch):
    monkeypatch.setattr(executor, "status", lambda forcar=False: ExecutorStatus(True, executor.imagem, lote=False))
    desfecho, recebidos = lote(executor, "def run(inputs, params):\n    return {}\n", [{}, {}])
    erro = recebidos[0][1].error
    assert desfecho == "concluido" and len(recebidos) == 1 and erro["category"] == "executor_indisponivel"
    assert "antiga" in erro["message"] and "build-executor" in erro["suggestion"]
