"""Executor isolado: roda código Python não confiável em um contêiner Docker.

A API NUNCA executa o código do usuário em seu próprio processo. Cada execução é um
contêiner novo e descartável, com:

* usuário sem privilégios (nobody, 65534) e ``--cap-drop ALL``;
* ``--network none`` (sem rede);
* sistema de arquivos somente leitura + um único tmpfs pequeno em /tmp (sem exec);
* limites de memória (sem swap), CPU, número de processos, arquivos e tempo de CPU;
* nenhum volume montado, nenhuma variável de ambiente do host, nenhum socket do Docker;
* trabalho entregue somente por stdin (nada do usuário vai para a linha de comando).

O host ainda impõe: tempo de parede (abate com ``docker kill``), teto de bytes lidos
de stdout/stderr e tamanho máximo da entrada.

Pool aquecido (``pool`` > 0): para esconder o custo de iniciar o Docker, alguns contêineres ficam já iniciados
esperando o trabalho no stdin (``pool.py``). A regra continua sendo UM contêiner por trabalho: quem recebe um trabalho
sai do pool para sempre e é removido ao terminar. Os limites do trabalho (tempo, CPU, vigia) só passam a valer quando
ele chega; até lá o contêiner ocioso se mata sozinho em ``pool_ocioso_s`` (e logo que a API morre).
Teto de contêineres vivos: no máximo 4 de trabalho (``max_paralelo``) + ``pool`` ociosos ou subindo.

Se o Docker ou a imagem não estiverem disponíveis, ``status()`` informa o motivo e
``run()`` falha de forma explícita — nunca há execução sem isolamento como alternativa.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, TypeGuard

from ..config import Limites
from .pool import Aquecido, PoolAquecido

log = logging.getLogger("trama.sandbox")

PREFIXO = "\x1e@@TRAMA@@"
ROTULO = "trama.executor=1"
# Mesma chave, outro valor: o saneamento de órfãos (``label=trama.executor``) cobre os dois, e quem olha o Docker
# distingue o contêiner de trabalho do que está só esperando.
ROTULO_POOL = "trama.executor=pool"
CHAVE_ROTULO = "trama.executor"
PRONTO = b"PRONTO"
PARTIDA_MAX_S = 30.0  # quanto esperar um contêiner do pool avisar que subiu

# PID 1 de um contêiner do pool (constante nossa, sem dado do usuário). Espera a linha de partida do host
# ``<vigia_s> <cpu_s>``; se ela não vier em ``$1`` segundos ou o host fechar o stdin (a API morreu), o contêiner sai.
# Chegando, aperta o limite de CPU para o valor do trabalho (só dá para diminuir o que o ``docker run`` fixou) e vira
# ``timeout`` por ``exec``, ainda como PID 1: o vigia do trabalho começa a contar AGORA, igual ao do caminho frio.
SCRIPT_DO_POOL = r"""echo PRONTO
trap 'exit 0' TERM
IFS=' ' read -r -t "$1" vigia cpu || exit 0
[[ $vigia =~ ^[0-9]+$ && $cpu =~ ^[0-9]+$ ]] || exit 64
ulimit -S -t "$cpu" && ulimit -H -t "$((cpu + 1))" || exit 65
exec timeout -s KILL "$vigia" python -I /opt/trama/runner.py"""

_SEGREDOS = [
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|senha|passwd)\b(\s*[=:]\s*)(\S+)"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
]


def limpar_segredos(texto: str) -> str:
    """Mascara padrões comuns de credenciais em textos de log/erro (camada extra)."""
    if not texto:
        return texto
    texto = _SEGREDOS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}[oculto]", texto)
    for padrao in _SEGREDOS[1:]:
        texto = padrao.sub("[oculto]", texto)
    return texto


@dataclass
class ExecutorStatus:
    disponivel: bool
    imagem: str
    motivo: str | None = None  # docker_ausente | daemon_inacessivel | imagem_ausente
    mensagem: str | None = None
    instrucao: str | None = None
    docker_versao: str | None = None

    def como_dict(self) -> dict[str, Any]:
        return {
            "disponivel": self.disponivel,
            "imagem": self.imagem,
            "motivo": self.motivo,
            "mensagem": self.mensagem,
            "instrucao": self.instrucao,
            "docker_versao": self.docker_versao,
        }


@dataclass
class SandboxResult:
    ok: bool
    payload: dict[str, Any] | None = None  # {"outputs": {...}} ou {"items": [...]}
    error: dict[str, Any] | None = None
    logs: list[dict[str, str]] = field(default_factory=list)  # {"source", "text"}
    duration_ms: int = 0


CATEGORIAS_DE_ERRO = frozenset({
    "excecao", "sintaxe", "tempo_esgotado", "memoria_excedida", "saida_excessiva",
    "retorno_invalido", "funcao_ausente", "erro_interno",
})


def _eh_int(v: Any) -> TypeGuard[int]:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10**9


def _texto(v: Any, limite: int) -> str:
    return v[:limite] if isinstance(v, str) else ""


def _erro(categoria: str, mensagem: str, **extra: Any) -> dict[str, Any]:
    base = {"category": categoria, "type": categoria, "message": mensagem,
            "line": None, "snippet": None, "traceback": ""}
    base.update(extra)
    return base


class _NaoEntregue(Exception):
    """O contêiner do pool morreu parado e não recebeu o trabalho: nada do código do usuário rodou."""


class DockerExecutor:
    def __init__(self, imagem: str, limites: Limites, docker_bin: str = "docker",
                 max_paralelo: int = 4, pool: int = 0, pool_ocioso_s: float = 120.0) -> None:
        self.imagem = imagem
        self.limites = limites
        self.docker_bin = docker_bin
        self._semaforo = threading.BoundedSemaphore(max_paralelo)
        self._cache: tuple[float, ExecutorStatus] | None = None
        self._lock = threading.Lock()
        self._pool = PoolAquecido(pool, pool_ocioso_s, self._iniciar_aquecido, self._descartar_aquecidos)

    # ------------------------------------------------------------------ status
    def status(self, forcar: bool = False) -> ExecutorStatus:
        agora = time.monotonic()
        with self._lock:
            if not forcar and self._cache and agora - self._cache[0] < 10:
                return self._cache[1]
        # A checagem chama o docker (pode demorar): não segura o lock, para não travar outras requisições.
        resultado = self._checar()
        with self._lock:
            self._cache = (time.monotonic(), resultado)
        return resultado

    def _checar(self) -> ExecutorStatus:
        base = ExecutorStatus(False, self.imagem)
        if shutil.which(self.docker_bin) is None:
            base.motivo = "docker_ausente"
            base.mensagem = "O Docker não foi encontrado neste computador."
            base.instrucao = ("Instale o Docker (https://docs.docker.com/get-docker/) e reinicie a Trama. "
                              "Enquanto isso, blocos com código Python ficam desabilitados.")
            return base
        try:
            versao = subprocess.run(
                [self.docker_bin, "version", "--format", "{{.Server.Version}}"],
                capture_output=True, text=True, timeout=8,
            )
        except (subprocess.TimeoutExpired, OSError):
            versao = None
        if versao is None or versao.returncode != 0 or not versao.stdout.strip():
            base.motivo = "daemon_inacessivel"
            base.mensagem = "O Docker está instalado, mas o serviço não respondeu."
            base.instrucao = ("Inicie o serviço do Docker (ex.: abra o Docker Desktop ou rode "
                              "`sudo systemctl start docker`) e confirme que seu usuário tem permissão de acesso. "
                              "Blocos com código Python ficam desabilitados até lá.")
            return base
        base.docker_versao = versao.stdout.strip()
        try:
            imagem = subprocess.run(
                [self.docker_bin, "image", "inspect", self.imagem],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            imagem = None
        if imagem is None or imagem.returncode != 0:
            base.motivo = "imagem_ausente"
            base.mensagem = f"A imagem do executor isolado ({self.imagem}) ainda não foi construída."
            base.instrucao = "Rode `./scripts/build-executor.sh` (ou `make executor`) e recarregue a página."
            return base
        base.disponivel = True
        return base

    # ----------------------------------------------------------------- limpeza
    def cleanup_orphans(self) -> int:
        """Remove contêineres de execuções anteriores que ficaram para trás."""
        if not self.status().disponivel:
            return 0
        try:
            ids = subprocess.run(
                [self.docker_bin, "ps", "-aq", "--filter", f"label={CHAVE_ROTULO}"],  # trabalho (=1) e pool (=pool)
                capture_output=True, text=True, timeout=10,
            ).stdout.split()
            if ids:
                subprocess.run([self.docker_bin, "rm", "-f", *ids], capture_output=True, timeout=30)
            return len(ids)
        except (subprocess.TimeoutExpired, OSError):
            return 0

    # --------------------------------------------------------------- execução
    @staticmethod
    def _cpu_brando(limites: Limites) -> int:
        return int(limites.tempo_s) + 3

    @staticmethod
    def _vigia_s(limites: Limites) -> int:
        # Vigia dentro do contêiner (`timeout` é o PID 1): só dispara se o host morrer, por isso fica
        # depois do abate normal feito pelo host.
        return int(limites.tempo_s + limites.folga_inicio_s) + 5

    def _opcoes(self, nome: str, limites: Limites, rotulo: str) -> list[str]:
        """Tudo o que isola o contêiner. É o mesmo para o caminho frio e para o pool: os dois passam por aqui."""
        mem = limites.memoria_mb + 64  # folga para o interpretador; o limite fino é o RLIMIT_AS do runner
        cpu_brando = self._cpu_brando(limites)
        return [
            self.docker_bin, "run", "-i",
            "--name", nome, "--label", rotulo,
            "--pull", "never",           # nunca baixar imagem no meio de uma execução
            "--log-driver", "none",      # a saída já vem pelo pipe; não duplica em disco no host
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--shm-size", "16m",
            "--memory", f"{mem}m", "--memory-swap", f"{mem}m",
            "--cpus", str(limites.cpus),
            "--pids-limit", str(limites.max_processos),
            # soft < hard: estourar o tempo de CPU recebe SIGXCPU (exit 152) antes do SIGKILL
            "--ulimit", f"cpu={cpu_brando}:{cpu_brando + 1}",
            "--ulimit", "nofile=256:256",
            "--ulimit", "fsize=16777216",
            "--ulimit", "core=0",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            "--ipc", "none",
        ]

    def _comando(self, nome: str, limites: Limites) -> list[str]:
        return [*self._opcoes(nome, limites, ROTULO), self.imagem,
                str(self._vigia_s(limites)), "python", "-I", "/opt/trama/runner.py"]

    def _comando_pool(self, nome: str) -> list[str]:
        """Contêiner ocioso: os limites de recursos são os do padrão do executor (o trabalho só pode apertá-los)."""
        return [*self._opcoes(nome, self.limites, ROTULO_POOL), "--entrypoint", "bash", self.imagem,
                "-c", SCRIPT_DO_POOL, "bash", f"{self._pool.ocioso_s:.3f}"]

    def _cabe_no_pool(self, limites: Limites) -> bool:
        """Só um trabalho cujos limites o contêiner ocioso já respeita (ou que são mais apertados) pode usá-lo.
        Memória, CPUs e processos são fixados pelo ``docker run`` e não dá para apertá-los depois; o tempo de CPU e o vigia
        são ajustados por trabalho na linha de partida."""
        base = self.limites
        return (limites.memoria_mb == base.memoria_mb and limites.cpus == base.cpus
                and limites.max_processos == base.max_processos
                and self._cpu_brando(limites) <= self._cpu_brando(base))

    def run(self, mode: str, code: str, inputs: dict | None = None,
            params: dict | None = None, items: list | None = None,
            limits: Limites | None = None) -> SandboxResult:
        """Executa ``codigo``. Nunca levanta exceção por problemas do usuário."""
        limites = limits or self.limites
        status = self.status()
        if not status.disponivel:
            return SandboxResult(False, error=_erro(
                "executor_indisponivel",
                status.mensagem or "O executor isolado não está disponível.",
                suggestion=status.instrucao))

        token = secrets.token_hex(12)
        trabalho = {
            "token": token, "mode": mode, "code": code,
            "inputs": inputs or {}, "params": params or {}, "items": items or [],
            "limits": {
                "time_s": limites.tempo_s, "memory_mb": limites.memoria_mb,
                "logs_max": limites.logs_max, "result_max": limites.valor_max,
                "rlimit_as": limites.rlimit_as,
            },
        }
        corpo = json.dumps(trabalho, ensure_ascii=True).encode("ascii")  # ASCII puro: sem surrogates nem separadores Unicode
        tamanho_max = limites.valor_max * 4
        if len(corpo) > tamanho_max:
            return SandboxResult(False, error=_erro(
                "valor_grande_demais",
                f"Os dados enviados ao código são grandes demais (limite de {tamanho_max // 1024} KB)."))

        with self._semaforo:
            aquecido = self._retirar_aquecido(limites)
            if aquecido is not None:
                try:
                    return self._rodar(corpo, token, limites, aquecido.nome, aquecido)
                except _NaoEntregue:
                    log.warning("O contêiner aquecido morreu antes de receber o trabalho; usando o caminho frio.")
                finally:
                    self._remover(aquecido.nome)
            nome = f"trama-{uuid.uuid4().hex[:12]}"
            try:
                return self._rodar(corpo, token, limites, nome)
            finally:
                self._remover(nome)  # sem --rm: o contêiner precisa existir até lermos o motivo do encerramento

    # ------------------------------------------------------------------- pool
    def aquecer(self) -> None:
        """Liga o pool (se configurado) e começa a encher em segundo plano. Idempotente."""
        self._pool.ativar()

    def encerrar(self) -> None:
        """Remove os contêineres ociosos e para a reposição. Um novo trabalho religa o pool sozinho."""
        self._pool.encerrar()

    def estado_pool(self) -> dict[str, Any]:
        return self._pool.estado()

    def _retirar_aquecido(self, limites: Limites) -> Aquecido | None:
        if self._pool.tamanho <= 0:
            return None
        self._pool.ativar()
        return self._pool.retirar() if self._cabe_no_pool(limites) else None

    def _iniciar_aquecido(self) -> Aquecido | None:
        """Sobe um contêiner ocioso e espera ele avisar (``PRONTO``) que está esperando o trabalho."""
        if not self.status().disponivel:
            return None
        nome = f"trama-{uuid.uuid4().hex[:12]}"
        criado_em = time.monotonic()
        try:
            proc = subprocess.Popen(self._comando_pool(nome), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
        except OSError as exc:
            log.warning("Falha ao iniciar o docker para o pool: %s", exc)
            return None
        saida = proc.stdout
        assert saida is not None  # Popen com stdout=PIPE
        linha: list[bytes] = []
        leitor = threading.Thread(target=lambda: linha.append(saida.readline()), daemon=True)
        leitor.start()
        leitor.join(PARTIDA_MAX_S)
        aquecido = Aquecido(nome, proc, criado_em)
        if linha and linha[0].strip() == PRONTO:
            return aquecido
        with contextlib.suppress(OSError):
            proc.kill()  # destrava o leitor se ele ainda esperava; o contêiner some em _descartar_aquecidos
        erro = proc.stderr.read(2000) if proc.stderr else b""
        log.warning("O contêiner do pool não ficou pronto: %s", limpar_segredos(erro.decode("utf-8", "replace")).strip())
        self._descartar_aquecidos([aquecido])
        return None

    def _descartar_aquecidos(self, aquecidos: list[Aquecido]) -> None:
        """Remove contêineres ociosos (mortos, vencidos ou sobrando no desligamento)."""
        if not aquecidos:
            return
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            subprocess.run([self.docker_bin, "rm", "-f", *[q.nome for q in aquecidos]], capture_output=True, timeout=30)
        for q in aquecidos:
            with contextlib.suppress(OSError):
                q.proc.kill()
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                q.proc.wait(timeout=5)
            for fluxo in (q.proc.stdin, q.proc.stdout, q.proc.stderr):
                if fluxo is not None:
                    with contextlib.suppress(OSError, ValueError):
                        fluxo.close()

    def _remover(self, nome: str) -> None:
        try:
            subprocess.run([self.docker_bin, "rm", "-f", nome], capture_output=True, timeout=15)
        except (subprocess.TimeoutExpired, OSError):
            log.warning("Não foi possível remover o contêiner %s (a limpeza da próxima inicialização cuida dele).", nome)

    def _oom(self, nome: str) -> bool:
        try:
            r = subprocess.run([self.docker_bin, "inspect", "-f", "{{.State.OOMKilled}}", nome],
                               capture_output=True, text=True, timeout=10)
            return r.stdout.strip() == "true"
        except (subprocess.TimeoutExpired, OSError):
            return False

    def _rodar(self, corpo: bytes, token: str, limites: Limites, nome: str,
               aquecido: Aquecido | None = None) -> SandboxResult:
        # O resultado pode crescer até ~6x ao ser escapado em JSON (controles viram \u00XX); o teto cobre isso
        # para não abater por engano uma resposta legítima.
        cap_stdout = 6 * (limites.valor_max + limites.logs_max) + 65536
        cap_stderr = 65536
        saida, erro_bytes = bytearray(), bytearray()
        estourou = threading.Event()

        def drenar(fluxo, destino: bytearray, teto: int) -> None:
            try:
                while True:
                    pedaco = fluxo.read(65536)
                    if not pedaco:
                        return
                    livre = teto - len(destino)
                    if livre > 0:
                        destino.extend(pedaco[:livre])
                    if len(pedaco) > livre:
                        estourou.set()
            except (OSError, ValueError):
                return

        inicio = time.monotonic()  # o tempo de parede do trabalho conta daqui, não de quando o contêiner ocioso subiu
        envio = corpo
        if aquecido is None:
            try:
                proc = subprocess.Popen(
                    self._comando(nome, limites),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
            except OSError as exc:
                log.error("Falha ao iniciar o docker: %s", exc)
                return SandboxResult(False, error=_erro(
                    "executor_indisponivel", "Não foi possível iniciar o executor isolado."))
        else:
            proc = aquecido.proc
            # A linha de partida só leva números calculados aqui; o trabalho (JSON) vem logo depois, como no caminho frio.
            envio = f"{self._vigia_s(limites)} {self._cpu_brando(limites)}\n".encode("ascii") + corpo

        entrada = proc.stdin
        assert entrada is not None  # Popen com stdin=PIPE
        falha_no_envio = threading.Event()

        def escrever() -> None:
            try:
                entrada.write(envio)
                entrada.close()
            except (BrokenPipeError, OSError, ValueError):
                falha_no_envio.set()

        threads = [
            threading.Thread(target=escrever, daemon=True),
            threading.Thread(target=drenar, args=(proc.stdout, saida, cap_stdout), daemon=True),
            threading.Thread(target=drenar, args=(proc.stderr, erro_bytes, cap_stderr), daemon=True),
        ]
        for t in threads:
            t.start()

        limite_parede = limites.tempo_s + limites.folga_inicio_s
        motivo_abate: str | None = None
        while True:
            try:
                proc.wait(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                pass
            if estourou.is_set():
                motivo_abate = "saida_excessiva"
                break
            if time.monotonic() - inicio > limite_parede:
                motivo_abate = "tempo_esgotado"
                break

        if motivo_abate:
            self._abater(nome, proc)
        for t in threads:
            t.join(timeout=2)
        duracao = int((time.monotonic() - inicio) * 1000)
        # Com o cliente já encerrado, o trabalho (JSON completo) não chegou ao runner: nada do usuário rodou.
        if aquecido is not None and motivo_abate is None and falha_no_envio.is_set() and not saida:
            raise _NaoEntregue

        if motivo_abate == "tempo_esgotado":
            return SandboxResult(False, duration_ms=duracao, error=_erro(
                "tempo_esgotado",
                f"O tempo máximo de {limites.tempo_s:g} s foi excedido e a execução foi encerrada.",
                suggestion="Verifique se há um laço infinito (while True) ou reduza a quantidade de dados."))
        if motivo_abate == "saida_excessiva":
            return SandboxResult(False, duration_ms=duracao, error=_erro(
                "saida_excessiva",
                "O código gerou mais saída do que o permitido e foi encerrado.",
                suggestion="Reduza o uso de print() e o tamanho dos dados devolvidos."))

        texto = saida.decode("utf-8", "replace")
        marcador = PREFIXO + token
        pos = texto.rfind(marcador)
        if pos >= 0:
            try:
                # split("\n") e não splitlines(): U+2028/U+2029/U+0085 em textos legítimos também "quebram linha"
                resposta = json.loads(texto[pos + len(marcador):].split("\n", 1)[0])
            except ValueError:
                resposta = None
            if isinstance(resposta, dict):
                return self._converter(resposta, duracao, limites)

        stderr_texto = limpar_segredos(erro_bytes.decode("utf-8", "replace")).strip()
        codigo = proc.returncode
        log.warning("Executor terminou sem resultado (código %s): %s", codigo, stderr_texto[-500:])
        if codigo == 137 and self._oom(nome):
            return SandboxResult(False, duration_ms=duracao, error=_erro(
                "memoria_excedida",
                f"O código foi encerrado por usar mais memória que o limite ({limites.memoria_mb} MB).",
                suggestion="Processe menos dados de uma vez ou evite criar estruturas muito grandes."))
        if codigo == 152:  # SIGXCPU: estourou o tempo de CPU
            return SandboxResult(False, duration_ms=duracao, error=_erro(
                "tempo_esgotado",
                f"O código usou mais tempo de processamento que o limite ({limites.tempo_s:g} s) e foi encerrado.",
                suggestion="Verifique se há um laço infinito (while True) ou reduza a quantidade de dados."))
        if codigo in (137, 143):
            return SandboxResult(False, duration_ms=duracao, error=_erro(
                "encerrado_pelo_sistema",
                "O código foi encerrado pelo sistema antes de terminar.",
                suggestion="Isso costuma indicar uso excessivo de recursos. Simplifique o código ou reduza os dados.",
                traceback=stderr_texto[-500:]))
        if codigo in (125, 126, 127):
            return SandboxResult(False, duration_ms=duracao, error=_erro(
                "executor_indisponivel",
                "Não foi possível iniciar o contêiner do executor isolado.",
                traceback=stderr_texto[-500:]))
        return SandboxResult(False, duration_ms=duracao, error=_erro(
            "erro_interno",
            "O executor terminou de forma inesperada, sem devolver resultado.",
            traceback=stderr_texto[-1500:]))

    def _converter(self, r: dict, duracao: int, limites: Limites) -> SandboxResult:
        """Converte a resposta do runner. O código do usuário roda no mesmo processo do runner, então ele
        PODE forjar essa resposta: nada aqui é confiável. Só tipos e tamanhos conhecidos passam adiante."""
        logs: list[dict[str, str]] = []
        restante = limites.logs_max
        for p in r.get("logs", []) if isinstance(r.get("logs"), list) else []:
            if not isinstance(p, dict) or p.get("source") not in ("stdout", "stderr") or not isinstance(p.get("text"), str):
                continue
            texto = p["text"].encode("utf-8", "replace")[:restante].decode("utf-8", "ignore")
            restante -= len(texto.encode("utf-8"))
            if texto:
                logs.append({"source": p["source"], "text": limpar_segredos(texto)})
            if restante <= 0:
                break
        if r.get("ok") is True:
            payload = r.get("payload")
            saidas, itens = (payload.get("outputs"), payload.get("items")) if isinstance(payload, dict) else (None, None)
            if isinstance(payload, dict) and not payload:
                payload = {}  # modo "check": só confirma que o código carrega
            elif isinstance(saidas, dict):
                payload = {"outputs": saidas}
            elif isinstance(itens, list):
                payload = {"items": itens}
            else:
                return SandboxResult(False, logs=logs, duration_ms=duracao, error=_erro(
                    "erro_interno", "O executor devolveu um resultado em formato inesperado."))
            duracao_runner = r.get("duration_ms")
            return SandboxResult(True, payload=payload, logs=logs,
                                 duration_ms=duracao_runner if _eh_int(duracao_runner) else duracao)
        bruto = r.get("error")
        if not isinstance(bruto, dict):
            bruto = {}
        categoria = bruto.get("category")
        erro = {
            "category": categoria if categoria in CATEGORIAS_DE_ERRO else "excecao",
            "type": _texto(bruto.get("type"), 200),
            "message": limpar_segredos(_texto(bruto.get("message"), 2000)) or "Erro desconhecido no executor.",
            "line": bruto.get("line") if _eh_int(bruto.get("line")) else None,
            "snippet": _texto(bruto.get("snippet"), 500) or None,
            "traceback": limpar_segredos(_texto(bruto.get("traceback"), 6000)),
        }
        if _eh_int(bruto.get("item_index")):
            erro["item_index"] = bruto["item_index"]
        return SandboxResult(False, error=erro, logs=logs, duration_ms=duracao)

    def _abater(self, nome: str, proc: subprocess.Popen) -> None:
        """Encerra o contêiner (matar só o cliente `docker run` NÃO mata o contêiner)."""
        for _ in range(5):
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                subprocess.run([self.docker_bin, "kill", nome], capture_output=True, timeout=10)
            try:
                proc.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                continue
        else:
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired, OSError):
            subprocess.run([self.docker_bin, "rm", "-f", nome], capture_output=True, timeout=10)
