"""Runner do executor isolado da Trama.

Este arquivo roda DENTRO do contêiner (usuário sem privilégios, sem rede, sistema
de arquivos somente leitura). Ele não importa nada do restante do projeto.

Protocolo
---------
* stdin : um único JSON com o trabalho
          ``{token, mode, code, inputs, params, items, limits}``.
          ``mode`` é ``block`` (chama run), ``map`` (chama transformar em cada item),
          ``check`` (carrega o código como na execução real e confere a função)
          ou ``batch`` (chama run uma vez por elemento de ``items``, em ordem).
* stdout: texto livre do código do usuário que escape da captura (ex.: escrita
          direta no descritor 1) e, na última linha, a resposta do runner no
          formato  ``<PREFIXO><token><json>``.  O token é aleatório por execução
          e só serve para o host distinguir a linha do runner de ruído.
          No modo ``batch`` há UMA linha dessas por item, escrita assim que o item
          termina (``{"i": posição, "ok": ..., ...}``), e uma última ``{"fim": true}``;
          o primeiro item que falha encerra o lote.

Modo batch: o que vale por item e o que vaza entre itens
--------------------------------------------------------
Cada item é uma chamada separada de ``run`` no MESMO processo. Valem POR ITEM, como num
contêiner só dele: o tempo (``time_s``), o teto de logs, o tamanho do resultado, a
captura de stdout/stderr e o módulo do usuário, que é executado do zero (variáveis globais,
funções e classes definidas por ele não passam de um item para o outro). ``params`` chega
como uma cópia nova a cada item e o ``/tmp`` é esvaziado entre os itens. O lote ainda tem
um tempo total (``batch_time_s``).
O que NÃO é refeito entre itens, porque pertence ao processo e não ao módulo do usuário:
o estado global da biblioteca padrão (por exemplo a semente do ``random``, a precisão do
``decimal``, o que o código atribuir a atributos de módulos como ``math`` ou ``json``) e os
módulos já importados. Código que depende de começar sempre "limpo" nesse sentido precisa
ajustar esse estado ele mesmo, dentro de ``run``.

Importante: a barreira de segurança é o contêiner (ver ``DockerExecutor``).
A lista de bibliotecas permitidas abaixo é uma camada extra de usabilidade
("defesa em profundidade") e NÃO deve ser tratada como isolamento.
"""

import builtins
import gc
import inspect
import json
import os
import resource
import shutil
import signal
import sys
import time
import traceback

PREFIXO = "\x1e@@TRAMA@@"
ARQUIVO = "<bloco>"

# Bibliotecas padrão liberadas para o código do usuário. Nada além da biblioteca
# padrão está instalado na imagem.
PERMITIDAS = frozenset(
    {
        "abc", "base64", "bisect", "calendar", "cmath", "collections", "contextlib",
        "copy", "csv", "dataclasses", "datetime", "decimal", "difflib", "enum",
        "fractions", "functools", "hashlib", "heapq", "html", "io", "itertools",
        "json", "math", "numbers", "operator", "pprint", "random", "re",
        "statistics", "string", "textwrap", "time", "typing", "unicodedata", "uuid",
    }
)


class TempoEsgotado(BaseException):
    """Limite brando de tempo. BaseException para não ser engolida por `except Exception`."""


class SaidaExcessiva(Exception):
    """O código imprimiu mais texto do que o permitido."""


class RetornoInvalido(Exception):
    """O retorno de run() não segue o contrato (não é dict ou não é JSON)."""


class ErroDeContrato(Exception):
    """A função existe mas não segue o contrato (ex.: parâmetros errados)."""

    def __init__(self, mensagem, linha=None, trecho=None):
        super().__init__(mensagem)
        self.linha = linha
        self.trecho = trecho


class ErroNoItem(Exception):
    """Exceção do usuário dentro de `transformar`, com o índice do item."""

    def __init__(self, original, indice):
        super().__init__(str(original))
        self.original = original
        self.indice = indice


class Logs:
    """Captura stdout/stderr do código do usuário com um teto de bytes."""

    def __init__(self, limite):
        self.limite = limite
        self.usado = 0
        self.pedacos = []

    def adicionar(self, origem, texto):
        if not texto:
            return
        self.usado += len(texto.encode("utf-8", "replace"))
        if self.usado > self.limite:
            raise SaidaExcessiva(
                "O bloco imprimiu texto demais (limite de %d KB). "
                "Reduza o uso de print()." % (self.limite // 1024)
            )
        if self.pedacos and self.pedacos[-1]["source"] == origem:
            self.pedacos[-1]["text"] += texto
        else:
            self.pedacos.append({"source": origem, "text": texto})


class Fluxo:
    def __init__(self, logs, origem):
        self._logs = logs
        self._origem = origem

    def write(self, texto):
        if not isinstance(texto, str):
            raise TypeError("write() argument must be str, not %s" % type(texto).__name__)
        self._logs.adicionar(self._origem, texto)
        return len(texto)

    def flush(self):
        pass

    def isatty(self):
        return False

    encoding = "utf-8"


def _permitida(nome):
    return nome.split(".")[0] in PERMITIDAS


def _importador(real):
    def importar(nome, globais=None, locais=None, fromlist=(), level=0):
        if level == 0 and not _permitida(nome):
            raiz = nome.split(".")[0]
            raise ImportError(
                "A biblioteca '%s' não está disponível neste ambiente. "
                "Bibliotecas permitidas: %s." % (raiz, ", ".join(sorted(PERMITIDAS)))
            )
        return real(nome, globais, locais, fromlist, level)

    return importar


def _builtins_do_usuario():
    ambiente = dict(vars(builtins))
    ambiente["__import__"] = _importador(builtins.__import__)
    return ambiente


def _emitir(token, resposta):
    sys.__stdout__.write("\n" + PREFIXO + token + json.dumps(resposta, ensure_ascii=False) + "\n")
    sys.__stdout__.flush()


def _achar_nao_serializavel(valor, caminho="retorno"):
    """Procura o primeiro valor que o JSON não representa, para a mensagem de erro."""
    if valor is None or isinstance(valor, (bool, int, str)):
        return None
    if isinstance(valor, float):
        if valor != valor or valor in (float("inf"), float("-inf")):
            return (caminho, "número não finito (NaN/infinito)")
        return None
    if isinstance(valor, (list, tuple)):
        for i, item in enumerate(valor):
            achado = _achar_nao_serializavel(item, "%s[%d]" % (caminho, i))
            if achado:
                return achado
        return None
    if isinstance(valor, dict):
        for chave, item in valor.items():
            if not isinstance(chave, str):
                return ("%s[%r]" % (caminho, chave), "chave do tipo %s (use texto)" % type(chave).__name__)
            achado = _achar_nao_serializavel(item, "%s[%r]" % (caminho, chave))
            if achado:
                return achado
        return None
    return (caminho, "valor do tipo %s" % type(valor).__name__)


def _serializar(valor, limite):
    try:
        texto = json.dumps(valor, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        achado = _achar_nao_serializavel(valor)
        onde, motivo = achado if achado else ("retorno", "valor não serializável")
        onde = onde.replace("retorno['outputs']", "saídas").replace("retorno['items']", "itens")
        raise RetornoInvalido(
            "O resultado precisa ser serializável em JSON (texto, número, booleano, "
            "lista, dicionário ou None). Problema em %s: %s." % (onde, motivo)
        )
    if len(texto.encode("utf-8")) > limite:
        raise RetornoInvalido(
            "O resultado ficou grande demais (limite de %d KB)." % (limite // 1024)
        )
    return texto


def _descrever(exc, linhas, categoria="excecao"):
    tb = exc.__traceback__
    # descarta quadros internos do runner antes do primeiro quadro do usuário
    while tb is not None and tb.tb_frame.f_code.co_filename != ARQUIVO:
        tb = tb.tb_next
    linha = None
    percorrido = tb
    while percorrido is not None:
        if percorrido.tb_frame.f_code.co_filename == ARQUIVO:
            linha = percorrido.tb_lineno
        percorrido = percorrido.tb_next
    if isinstance(exc, SyntaxError) and exc.filename == ARQUIVO:
        linha = exc.lineno
        categoria = "sintaxe"
    trecho = None
    if linha and 1 <= linha <= len(linhas):
        trecho = linhas[linha - 1].strip()
    # monta o traceback sem os quadros internos do runner (caminhos do contêiner não ajudam o usuário)
    quadros = [q for q in traceback.extract_tb(tb) if q.filename == ARQUIVO or not q.filename.endswith("runner.py")]
    partes = []
    if quadros:
        partes.append("Traceback (most recent call last):\n")
        partes.extend(traceback.format_list(quadros))
    partes.extend(traceback.format_exception_only(type(exc), exc))
    return {
        "category": categoria,
        "type": type(exc).__name__,
        "message": str(exc)[:2000],
        "line": linha,
        "snippet": trecho,
        "traceback": "".join(partes)[-6000:],
    }


def _erro_simples(categoria, tipo, mensagem, linha=None, trecho=None):
    return {"category": categoria, "type": tipo, "message": mensagem,
            "line": linha, "snippet": trecho, "traceback": ""}


def _aceita_dois(funcao):
    try:
        parametros = list(inspect.signature(funcao).parameters.values())
    except (TypeError, ValueError):
        return False
    posicionais = [p for p in parametros if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return len(posicionais) >= 2 or any(p.kind == p.VAR_POSITIONAL for p in parametros)


def _carregar(codigo, nome, compilar=None):
    """Compila e executa o código do módulo (como na execução real) e devolve a função pedida.

    ``compilar`` devolve o código já compilado: o lote compila uma vez e executa o módulo do zero a cada item."""
    ambiente = {"__name__": "__bloco__", "__builtins__": _builtins_do_usuario()}
    exec(compilar() if compilar else compile(codigo, ARQUIVO, "exec"), ambiente)
    funcao = ambiente.get(nome)
    if not callable(funcao):
        raise NameError(
            "O código precisa definir a função `def transformar(item):`."
            if nome == "transformar"
            else "O código precisa definir a função `def run(inputs, params):`."
        )
    return funcao


def _executar(trabalho, codigo, limites, linhas, compilar=None):
    """Executa o código do usuário e devolve o payload (dict) ou levanta uma exceção."""
    modo = trabalho.get("mode", "block")
    funcao = _carregar(codigo, "transformar" if modo == "map" else "run", compilar)
    if modo == "check":
        if not _aceita_dois(funcao):
            linha = getattr(getattr(funcao, "__code__", None), "co_firstlineno", None)
            raise ErroDeContrato(
                "A função run precisa receber dois parâmetros: `def run(inputs, params):`.",
                linha, linhas[linha - 1].strip() if linha and 1 <= linha <= len(linhas) else None)
        return {}
    if modo == "map":
        dois = _aceita_dois(funcao)
        resultado = []
        for indice, item in enumerate(trabalho.get("items", [])):
            try:
                resultado.append(funcao(item, indice) if dois else funcao(item))
            except (TempoEsgotado, SaidaExcessiva, MemoryError):
                raise
            except Exception as exc:
                raise ErroNoItem(exc, indice) from None
        payload = {"items": resultado}
    else:
        retorno = funcao(trabalho.get("inputs", {}), trabalho.get("params", {}))
        if not isinstance(retorno, dict):
            raise RetornoInvalido(
                "A função run deve devolver um dicionário com as saídas, "
                "mas devolveu %s." % type(retorno).__name__
            )
        payload = {"outputs": retorno}
    texto = _serializar(payload, int(limites.get("result_max", 1024 * 1024)))
    return json.loads(texto)


def _rodar_trabalho(trabalho, codigo, limites, linhas, logs, tempo_s, memoria_mb, compilar=None, tempo_do_lote=None):
    """Executa um trabalho (ou um item do lote) com stdout/stderr capturados e o aviso de tempo armado.

    Devolve a resposta do runner, com os logs. ``tempo_do_lote`` só vale quando o tempo que restava do lote é menor
    que o de um item: se o aviso disparar, a mensagem diz que foi o tempo total que acabou."""
    stdout_real, stderr_real = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = Fluxo(logs, "stdout"), Fluxo(logs, "stderr")
    # re-arma a cada 250 ms: um `except BaseException` que engole o aviso é interrompido de novo
    signal.setitimer(signal.ITIMER_REAL, max(tempo_s, 0.05), 0.25)
    inicio = time.monotonic()
    try:
        payload = _executar(trabalho, codigo, limites, linhas, compilar)
        resposta = {"ok": True, "payload": payload,
                    "duration_ms": int((time.monotonic() - inicio) * 1000)}
    except TempoEsgotado as exc:
        info = _descrever(exc, linhas, "tempo_esgotado")
        if tempo_do_lote is None:
            info["message"] = "O tempo máximo de %g s foi excedido." % tempo_s
        else:
            info["type"] = "TempoDoLote"
            info["message"] = _mensagem_tempo_do_lote(tempo_do_lote)
        resposta = {"ok": False, "error": info}
    except SaidaExcessiva as exc:
        resposta = {"ok": False, "error": _descrever(exc, linhas, "saida_excessiva")}
    except MemoryError as exc:
        info = _descrever(exc, linhas, "memoria_excedida")
        info["message"] = "O limite de memória (%d MB) foi excedido." % memoria_mb
        resposta = {"ok": False, "error": info}
    except RetornoInvalido as exc:
        resposta = {"ok": False, "error": _descrever(exc, linhas, "retorno_invalido")}
    except ErroDeContrato as exc:
        resposta = {"ok": False, "error": _erro_simples(
            "funcao_ausente", "AssinaturaInvalida", str(exc), exc.linha, exc.trecho)}
    except ErroNoItem as exc:
        info = _descrever(exc.original, linhas)
        info["item_index"] = exc.indice
        resposta = {"ok": False, "error": info}
    except NameError as exc:
        ausente = "def run" in str(exc) or "def transformar" in str(exc)
        resposta = {"ok": False, "error": _descrever(exc, linhas, "funcao_ausente" if ausente else "excecao")}
    except BaseException as exc:
        resposta = {"ok": False, "error": _descrever(exc, linhas)}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        sys.stdout, sys.stderr = stdout_real, stderr_real
    resposta["logs"] = logs.pedacos
    return resposta


def _mensagem_tempo_do_lote(tempo_do_lote):
    return ("O tempo total do laço (%g s, somando todos os itens) foi excedido; o item em andamento foi interrompido."
            % tempo_do_lote)


def _limpar_tmp():
    """Esvazia /tmp (a única pasta gravável do contêiner), para o próximo item começar como num contêiner novo."""
    try:
        presentes = list(os.scandir("/tmp"))
    except OSError:
        return
    for entrada in presentes:
        try:
            if entrada.is_dir(follow_symlinks=False):
                shutil.rmtree(entrada.path, ignore_errors=True)
            else:
                os.unlink(entrada.path)
        except Exception:  # nada que o item deixou em /tmp pode derrubar o runner entre dois itens
            pass


def _executar_lote(token, trabalho, codigo, limites, linhas, memoria_mb):
    """Modo ``batch``: chama ``run`` uma vez por item, na ordem, e escreve a resposta de cada item assim que ele termina.

    O primeiro item que falha encerra o lote (os seguintes nem começam, como num laço que para na primeira falha).
    A resposta de cada item sai na hora: se o contêiner for abatido no meio, o host já tem o que os itens anteriores devolveram."""
    itens = trabalho.get("items", [])
    tempo_item = float(limites.get("time_s", 10))
    tempo_lote = float(limites.get("batch_time_s", tempo_item * max(len(itens), 1)))
    logs_max = int(limites.get("logs_max", 64 * 1024))
    params = json.dumps(trabalho.get("params", {}))
    compilado = []

    def compilar():
        # dentro do tempo e da captura do primeiro item: um erro de sintaxe é a falha dele, como no trabalho avulso
        if not compilado:
            compilado.append(compile(codigo, ARQUIVO, "exec"))
        return compilado[0]

    gc.collect()
    gc.freeze()  # o que o runner já carregou sai das varreduras: o gc.collect() entre itens só olha o que o item criou
    inicio = time.monotonic()
    feitos = 0
    for indice, entradas in enumerate(itens):
        logs = Logs(logs_max)
        restante = tempo_lote - (time.monotonic() - inicio)
        if restante <= 0:
            resposta = {"ok": False, "logs": [], "error": _erro_simples(
                "tempo_esgotado", "TempoDoLote", _mensagem_tempo_do_lote(tempo_lote))}
        else:
            do_lote = tempo_lote if restante < tempo_item else None
            item = {"mode": "block", "inputs": entradas, "params": json.loads(params)}
            try:
                resposta = _rodar_trabalho(item, codigo, limites, linhas, logs, min(tempo_item, restante), memoria_mb,
                                           compilar, do_lote)
            except BaseException as exc:  # o aviso de tempo chegou durante o tratamento de outro erro
                signal.setitimer(signal.ITIMER_REAL, 0)
                if isinstance(exc, TempoEsgotado):
                    erro = _erro_simples("tempo_esgotado", "TempoEsgotado", "O tempo máximo de %g s foi excedido." % tempo_item)
                else:
                    erro = {"category": "erro_interno", "type": type(exc).__name__, "message": str(exc)[:500],
                            "line": None, "snippet": None, "traceback": traceback.format_exc()[-3000:]}
                resposta = {"ok": False, "logs": logs.pedacos, "error": erro}
        resposta["i"] = indice
        _emitir(token, resposta)
        feitos += 1
        if not resposta["ok"]:
            break
        _limpar_tmp()
        gc.collect()
    _emitir(token, {"fim": True, "total": feitos})


def main():
    token = ""
    logs = Logs(64 * 1024)
    linhas = []
    tempo_s = 10.0
    stdout_real, stderr_real = sys.stdout, sys.stderr
    try:
        trabalho = json.loads(sys.stdin.read())
        token = trabalho.get("token", "")
        codigo = trabalho.get("code", "")
        limites = trabalho.get("limits", {})
        logs = Logs(int(limites.get("logs_max", 64 * 1024)))
        linhas = codigo.splitlines()
        tempo_s = float(limites.get("time_s", 10))
        memoria_mb = int(limites.get("memory_mb", 256))

        if limites.get("rlimit_as", True):
            limite_bytes = memoria_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limite_bytes, limite_bytes))

        def _estourou(*_):
            raise TempoEsgotado()

        signal.signal(signal.SIGALRM, _estourou)
        if trabalho.get("mode") == "batch":
            _executar_lote(token, trabalho, codigo, limites, linhas, memoria_mb)
            return
        _emitir(token, _rodar_trabalho(trabalho, codigo, limites, linhas, logs, tempo_s, memoria_mb))
    except BaseException as exc:
        signal.setitimer(signal.ITIMER_REAL, 0)
        sys.stdout, sys.stderr = stdout_real, stderr_real
        if isinstance(exc, TempoEsgotado):  # o aviso chegou durante o tratamento de outro erro
            _emitir(token, {"ok": False, "logs": logs.pedacos, "error": _erro_simples(
                "tempo_esgotado", "TempoEsgotado", "O tempo máximo de %g s foi excedido." % tempo_s)})
            return
        _emitir(token, {"ok": False, "logs": logs.pedacos, "error": {
            "category": "erro_interno", "type": type(exc).__name__, "message": str(exc)[:500],
            "line": None, "snippet": None, "traceback": traceback.format_exc()[-3000:]}})


if __name__ == "__main__":
    main()
