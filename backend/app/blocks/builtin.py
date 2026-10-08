"""Blocos internos da biblioteca.

Os blocos "de dados" (texto, matemática, selecionar campos, transformar lista…) são código NOSSO e rodam
no processo da API. Por isso são deliberadamente limitados: não avaliam expressões nem expressões
regulares fornecidas pelo usuário e limitam o tamanho das contas. Qualquer coisa escrita pelo usuário
em Python roda exclusivamente no executor isolado.

Os blocos de controle (condição, para cada, repetir até, escopo, encerrar) e as variáveis são executados
pelo motor (``engine.py``), que conhece a árvore de passos; aqui ficam só as suas definições.
"""

from __future__ import annotations

import json
import math
import string
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import Limites
from ..errors import ErroBloco
from ..models import BlockType, Option, ParamDef, PortDef, SlotDef, TypeFrom, VisibleWhen
from ..tipos import descrever_valor, tipo_do_valor

# Cada bloco tem uma versão de contrato; mudar o contrato exige nova versão.
VERSAO = 1


@dataclass
class ContextoBloco:
    """O que um bloco interno pode usar além de suas entradas e parâmetros."""

    limites: Limites
    # (código, itens, params) -> lista transformada; levanta ErroBloco.
    mapa_python: Callable[[str, list, dict], list] | None = None
    # Valores informados ao gatilho nesta execução.
    dados_gatilho: dict[str, Any] | None = None
    # Logs produzidos durante o passo (preenchido pelo motor, ex.: saída do código Python).
    logs: list[dict[str, str]] = field(default_factory=list)


Handler = Callable[[dict[str, Any], dict[str, Any], ContextoBloco], dict[str, Any]]
Validador = Callable[[dict[str, Any]], list[tuple[str | None, str]]]


# ------------------------------------------------------------------------------ utilidades
def _opcoes(*pares: tuple[str, str]) -> list[Option]:
    return [Option(value=v, label=l) for v, l in pares]


def _numero(r: Any) -> Any:
    """Normaliza resultados: 4.0 vira 4; estouro/NaN viram erro compreensível."""
    if isinstance(r, float):
        if not math.isfinite(r):
            raise ErroBloco("O resultado não é um número válido (houve estouro ou indefinição).",
                            codigo="resultado_invalido")
        if r.is_integer() and abs(r) < 1e15:
            return int(r)
    return r


def _eh_numero(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def texto_de(v: Any) -> str:
    """Como um valor aparece quando é colado dentro de um texto."""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "verdadeiro" if v else "falso"
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return str(_numero(v))
    return json.dumps(v, ensure_ascii=False)


def _parse_numero(bruto: str) -> int | float | None:
    t = bruto.strip().replace(",", ".")
    try:
        f = float(t)
    except ValueError:
        return None
    if not math.isfinite(f):
        return None
    return int(f) if f.is_integer() and abs(f) < 1e15 else f


def _igual(a: Any, b: Any) -> bool:
    """Igualdade que não confunde True com 1."""
    if isinstance(a, bool) != isinstance(b, bool):
        return False
    return a == b


def buscar_caminho(objeto: Any, caminho: str) -> Any:
    """Resolve `a.b.0.c` em dicionários e listas. Levanta KeyError/IndexError/TypeError."""
    atual = objeto
    for parte in caminho.split("."):
        if isinstance(atual, dict):
            atual = atual[parte]
        elif isinstance(atual, list):
            atual = atual[int(parte)]
        else:
            raise TypeError(parte)
    return atual


MAX_CAMINHOS = 50


def _exigir_tamanho(estimado: int, ctx: "ContextoBloco", o_que: str) -> None:
    """Blocos internos rodam no processo da API: o tamanho do resultado é conferido ANTES de montá-lo."""
    if estimado > ctx.limites.valor_max:
        raise ErroBloco(
            f"{o_que} ficaria grande demais (mais de {ctx.limites.valor_max // 1024} KB).",
            codigo="valor_grande_demais",
            sugestao="Use um texto/lista menor ou um trecho de substituição mais curto.")


def valor_padrao_do_tipo(tipo: str) -> Any:
    return {"texto": "", "numero": 0, "booleano": False, "lista": [], "json": {}}.get(tipo)


# ------------------------------------------------------------------------------ comparações (condição)
OPERADORES = _opcoes(
    ("igual", "é igual a"), ("diferente", "é diferente de"), ("maior", "é maior que"),
    ("maior_igual", "é maior ou igual a"), ("menor", "é menor que"), ("menor_igual", "é menor ou igual a"),
    ("contem", "contém"), ("nao_contem", "não contém"), ("vazio", "está vazio"),
    ("nao_vazio", "não está vazio"), ("verdadeiro", "é verdadeiro (sim)"), ("falso", "é falso (não)"),
)
OPERADORES_UNARIOS = frozenset({"vazio", "nao_vazio", "verdadeiro", "falso"})
OPERADORES_DE_ORDEM = frozenset({"maior", "maior_igual", "menor", "menor_igual"})
MAX_REGRAS = 10


def _coagir(dir_: Any, esq: Any) -> Any:
    """O valor digitado do outro lado ("200", "sim") assume o tipo do valor que está sendo testado."""
    if not isinstance(dir_, str):
        return dir_
    if isinstance(esq, bool):
        t = dir_.strip().lower()
        if t in ("true", "verdadeiro", "sim", "1"):
            return True
        if t in ("false", "falso", "nao", "não", "0"):
            return False
        raise ErroBloco(f"“{dir_}” não é um valor sim/não válido para comparar.", codigo="parametro_invalido",
                        sugestao="Use verdadeiro/falso (ou sim/não).")
    if _eh_numero(esq):
        n = _parse_numero(dir_)
        if n is None:
            raise ErroBloco(f"“{dir_}” não é um número válido para comparar.", codigo="parametro_invalido",
                            sugestao="Digite apenas o número, por exemplo 10 ou 2,5.")
        return n
    return dir_


def avaliar_regra(esq: Any, op: str, dir_: Any) -> bool:
    if op in ("vazio", "nao_vazio"):
        vazio = esq is None or esq == [] or esq == {} or (isinstance(esq, str) and esq.strip() == "")
        return vazio if op == "vazio" else not vazio
    if op in ("verdadeiro", "falso"):
        if not isinstance(esq, bool):
            raise ErroBloco(
                f"Esta condição espera um valor sim/não, mas recebeu {descrever_valor(esq)}.",
                codigo="tipo_invalido", sugestao="Use outro teste (ex.: “é igual a”) ou escolha um valor sim/não.")
        return esq if op == "verdadeiro" else not esq
    if op in ("contem", "nao_contem"):
        if isinstance(esq, str):
            achou = texto_de(dir_) in esq
        elif isinstance(esq, list):
            candidatos: list[Any] = [dir_]
            if isinstance(dir_, str):
                n = _parse_numero(dir_)
                if n is not None:
                    candidatos.append(n)
            achou = any(_igual(item, c) for item in esq for c in candidatos)
        elif isinstance(esq, dict):
            achou = texto_de(dir_) in esq
        else:
            raise ErroBloco(f"“contém” só funciona com texto, lista ou objeto, mas recebeu {descrever_valor(esq)}.",
                            codigo="tipo_invalido")
        return achou if op == "contem" else not achou
    ref = _coagir(dir_, esq)
    if op in ("igual", "diferente"):
        r = _igual(esq, ref)
        return r if op == "igual" else not r
    if op not in OPERADORES_DE_ORDEM:
        raise ErroBloco(f"Teste desconhecido: {op}.", codigo="parametro_invalido")
    if not ((_eh_numero(esq) and _eh_numero(ref)) or (isinstance(esq, str) and isinstance(ref, str))):
        raise ErroBloco(
            f"Não é possível comparar {descrever_valor(esq)} com {descrever_valor(ref)}.",
            codigo="tipo_invalido",
            sugestao="Faça os dois lados serem números (ou os dois serem textos).")
    return {"maior": esq > ref, "maior_igual": esq >= ref, "menor": esq < ref, "menor_igual": esq <= ref}[op]


def combinar_regras(combinador: str, resultados: list[bool]) -> bool:
    return all(resultados) if combinador != "ou" else any(resultados)


# ------------------------------------------------------------------------------ handlers
def _gatilho(inputs, params, ctx):
    """Devolve os valores informados ao gatilho (ou o padrão de cada campo)."""
    dados = ctx.dados_gatilho or {}
    saida: dict[str, Any] = {}
    for campo in params.get("campos") or []:
        cid = campo["id"]
        if cid in dados:
            saida[cid] = dados[cid]
        elif campo.get("default") is not None:
            saida[cid] = campo["default"]
        else:
            saida[cid] = valor_padrao_do_tipo(campo.get("type", "texto"))
    return saida


def _compor(inputs, params, ctx):
    return {"resultado": inputs["entrada"]}


def _potencia(a, b):
    if isinstance(a, int) and isinstance(b, int) and 0 <= b <= 64 and abs(a) <= 10**9:
        return a**b
    try:
        r = float(a) ** float(b)
    except OverflowError:
        raise ErroBloco("O resultado da potência é grande demais.", codigo="resultado_invalido") from None
    except ZeroDivisionError:
        raise ErroBloco("Zero não pode ser elevado a um expoente negativo.", codigo="divisao_por_zero") from None
    if isinstance(r, complex):
        raise ErroBloco("Não existe resultado real para essa potência (raiz de número negativo).",
                        codigo="resultado_invalido")
    return r


def _matematica(inputs, params, ctx):
    a, b = inputs["a"], inputs["b"]
    op = params.get("operacao", "somar")
    if op in ("dividir", "resto") and b == 0:
        raise ErroBloco("Não é possível dividir por zero.", codigo="divisao_por_zero",
                        sugestao="Confira o valor da entrada B: ele não pode ser 0.")
    try:
        if op == "somar":
            r = a + b
        elif op == "subtrair":
            r = a - b
        elif op == "multiplicar":
            r = a * b
        elif op == "dividir":
            r = a / b
        elif op == "resto":
            r = a % b
        elif op == "potencia":
            r = _potencia(a, b)
        elif op == "minimo":
            r = min(a, b)
        elif op == "maximo":
            r = max(a, b)
        else:  # protegido pela validação do parâmetro
            raise ErroBloco(f"Operação desconhecida: {op}.", codigo="parametro_invalido")
    except OverflowError:
        raise ErroBloco("O resultado é grande demais para ser representado.", codigo="resultado_invalido") from None
    return {"resultado": _numero(r)}


def _texto(inputs, params, ctx):
    t = inputs["texto"]
    op = params.get("operacao", "maiusculas")
    if op == "maiusculas":
        r = t.upper()
    elif op == "minusculas":
        r = t.lower()
    elif op == "titulo":
        r = string.capwords(t)
    elif op == "remover_espacos":
        r = t.strip()
    elif op == "inverter":
        r = t[::-1]
    elif op == "substituir":
        buscar, novo = params["buscar"], params.get("substituir_por", "")
        _exigir_tamanho(len(t) + t.count(buscar) * max(len(novo) - len(buscar), 0), ctx, "O texto resultante")
        r = t.replace(buscar, novo)
    elif op == "prefixo_sufixo":
        r = f"{params.get('prefixo', '')}{t}{params.get('sufixo', '')}"
    elif op == "concatenar":
        if "outro" not in inputs:
            raise ErroBloco("Para juntar textos, preencha o campo “Outro texto”.",
                            codigo="entrada_ausente")
        r = f"{t}{params.get('separador', '')}{inputs['outro']}"
    else:
        raise ErroBloco(f"Operação desconhecida: {op}.", codigo="parametro_invalido")
    return {"resultado": r, "tamanho": len(r)}


def _selecionar_campos(inputs, params, ctx):
    objeto = inputs["objeto"]
    caminhos = [c.strip() for c in str(params["caminhos"]).replace(",", "\n").splitlines() if c.strip()]
    if not caminhos:
        raise ErroBloco("Informe ao menos um campo para selecionar.", codigo="parametro_invalido")
    if len(caminhos) > MAX_CAMINHOS:
        raise ErroBloco(f"São campos demais ({len(caminhos)}): o máximo é {MAX_CAMINHOS} por bloco.",
                        codigo="parametro_invalido")
    nulo = params.get("se_ausente", "erro") == "nulo"
    selecionados: dict[str, Any] = {}
    estimado = 0
    for caminho in caminhos:
        try:
            selecionados[caminho] = buscar_caminho(objeto, caminho)
            if isinstance(selecionados[caminho], (dict, list)):  # o mesmo trecho grande pode ser pedido várias vezes
                estimado += len(json.dumps(selecionados[caminho], ensure_ascii=False))
                _exigir_tamanho(estimado, ctx, "O conjunto de campos selecionados")
        except (KeyError, IndexError, ValueError, TypeError):
            if nulo:
                selecionados[caminho] = None
                continue
            disponiveis = ", ".join(map(str, list(objeto.keys())[:10])) or "nenhum"
            raise ErroBloco(
                f"O campo “{caminho}” não existe nos dados recebidos.",
                codigo="campo_ausente",
                sugestao=f"Campos disponíveis no primeiro nível: {disponiveis}. "
                         "Use ponto para campos internos (ex.: endereco.cidade) e números para listas (ex.: itens.0).",
            ) from None
    return {"valor": selecionados[caminhos[0]], "selecionados": selecionados}


def _aplicar_item(op: str, item: Any, indice: int, params: dict[str, Any]) -> Any:
    def falha(motivo: str) -> ErroBloco:
        return ErroBloco(
            f"Não foi possível aplicar a operação ao item {indice + 1} da lista: {motivo}",
            codigo="item_invalido", tecnico={"item_index": indice, "item": item},
            sugestao="Confira se todos os itens da lista têm o tipo esperado pela operação.")

    if op in ("multiplicar", "somar", "subtrair", "dividir", "potencia"):
        if not _eh_numero(item):
            raise falha(f"esperava um número, mas o item é {descrever_valor(item)}.")
        n = params.get("operando", 0)
        if op == "multiplicar":
            return _numero(item * n)
        if op == "somar":
            return _numero(item + n)
        if op == "subtrair":
            return _numero(item - n)
        if op == "dividir":
            return _numero(item / n)
        return _numero(_potencia(item, n))
    if op in ("maiusculas", "minusculas", "remover_espacos"):
        if not isinstance(item, str):
            raise falha(f"esperava um texto, mas o item é {descrever_valor(item)}.")
        return {"maiusculas": item.upper, "minusculas": item.lower, "remover_espacos": item.strip}[op]()
    if op == "adicionar_texto":
        if isinstance(item, (list, dict)) or item is None:
            raise falha(f"esperava texto ou número, mas o item é {descrever_valor(item)}.")
        return f"{params.get('prefixo', '')}{texto_de(item)}{params.get('sufixo', '')}"
    if op == "para_texto":
        return texto_de(item)
    if op == "para_numero":
        if _eh_numero(item):
            return item
        n = _parse_numero(item) if isinstance(item, str) else None
        if n is None:
            raise falha("não é possível converter para número.")
        return n
    if op == "extrair_campo":
        try:
            return buscar_caminho(item, str(params["campo"]))
        except (KeyError, IndexError, ValueError, TypeError):
            raise falha(f"o campo “{params['campo']}” não existe neste item.") from None
    raise ErroBloco(f"Operação desconhecida: {op}.", codigo="parametro_invalido")


def _transformar_lista(inputs, params, ctx):
    lista = inputs["lista"]
    limite = int(params.get("limite", 100))
    teto = ctx.limites.itens_max_lista
    if limite > teto:
        raise ErroBloco(f"O limite de itens ({limite}) passa do máximo permitido ({teto}).", codigo="parametro_invalido")
    if len(lista) > limite:
        raise ErroBloco(
            f"A lista tem {len(lista)} itens, mas o limite configurado é {limite}.",
            codigo="limite_itens",
            sugestao=f"Aumente o “Limite de itens” no bloco (até {teto}) ou envie uma lista menor.")
    op = params.get("operacao", "multiplicar")
    if op == "python":
        if ctx.mapa_python is None:
            raise ErroBloco("O executor isolado não está disponível para rodar código Python.",
                            codigo="executor_indisponivel")
        resultado = ctx.mapa_python(str(params["codigo"]), lista, params)
    else:
        if op == "adicionar_texto":  # único que aumenta cada item: confere o total antes de montar
            extra = len(str(params.get("prefixo", ""))) + len(str(params.get("sufixo", "")))
            _exigir_tamanho(sum(len(texto_de(i)) + extra for i in lista if not isinstance(i, (list, dict))),
                            ctx, "A nova lista")
        resultado = [_aplicar_item(op, item, i, params) for i, item in enumerate(lista)]
    return {"resultado": resultado, "quantidade": len(resultado)}


def _saida(inputs, params, ctx):
    return {}


# ------------------------------------------------------------------------------ validadores de parâmetros
def _validar_transformar_lista(params):
    erros: list[tuple[str | None, str]] = []
    if params.get("operacao") == "dividir" and params.get("operando", 1) == 0:
        erros.append(("operando", "Não é possível dividir por zero: escolha um valor diferente de 0."))
    return erros


def _validar_variavel_nova(params):
    nome = str(params.get("nome", "")).strip()
    if nome and len(nome) > 40:
        return [("nome", "O nome da variável deve ter no máximo 40 caracteres.")]
    return []


# ------------------------------------------------------------------------------ definições
CODIGO_TRANSFORMAR = (
    "def transformar(item):\n"
    "    # Recebe cada item da lista e devolve o novo valor.\n"
    "    return item\n"
)

CODIGO_PYTHON = (
    "def run(inputs, params):\n"
    "    # `inputs` traz os campos declarados ao lado (opcionais não preenchidos não aparecem).\n"
    "    # Devolva um dicionário com exatamente as saídas declaradas.\n"
    "    nome = inputs.get(\"nome\", \"mundo\")\n"
    "    return {\"resultado\": f\"Olá, {nome}!\"}\n"
)

ENTRADAS_PYTHON = [{"id": "nome", "label": "Nome", "type": "texto", "required": False}]
SAIDAS_PYTHON = [{"id": "resultado", "label": "Resultado", "type": "texto"}]

OPERACOES_NUMERICAS = ["multiplicar", "somar", "subtrair", "dividir", "potencia"]
TIPOS_VARIAVEL = _opcoes(("texto", "Texto"), ("numero", "Número"), ("booleano", "Sim/Não"),
                         ("lista", "Lista"), ("json", "Objeto JSON"))

REGRA_PADRAO = [{"esq": {"value": ""}, "op": "igual", "dir": {"value": ""}}]


def _param_regras() -> ParamDef:
    return ParamDef(
        id="regras", label="Condições", type="regras", required=True, default=REGRA_PADRAO, options=OPERADORES,
        help="Compare valores fixos ou conteúdo dinâmico de passos anteriores.")


def _param_combinador() -> ParamDef:
    return ParamDef(id="combinador", label="Juntar condições com", type="selecao", default="e",
                    options=_opcoes(("e", "E (todas precisam ser verdadeiras)"),
                                    ("ou", "OU (basta uma ser verdadeira)")))


def _param_variavel() -> ParamDef:
    return ParamDef(id="variavel", label="Variável", type="variavel", required=True,
                    help="Escolha uma variável criada antes por “Inicializar variável”.")


def _definicoes() -> list[tuple[BlockType, Handler | None, Validador | None]]:
    gatilho = BlockType(
        id="builtin.gatilho_manual", version=VERSAO, name="Acionar manualmente", kind="builtin", category="Gatilhos",
        icon="play", trigger=True, outputs_from="campos",
        description="O fluxo começa quando você clica em Testar. Declare os campos que ele pede (texto, número, sim/não…).",
        params=[ParamDef(id="campos", label="Campos de entrada", type="portas", default=[],
                         help="Cada campo vira um conteúdo dinâmico disponível em todos os passos.")])

    condicao = BlockType(
        id="builtin.condicao", version=VERSAO, name="Condição", kind="builtin", category="Controle", icon="branch",
        description="Testa valores e segue por “Se sim” ou “Se não”. Só os passos do caminho escolhido são executados.",
        slots=[SlotDef(id="sim", label="Se sim", empty_hint="Adicione os passos que rodam quando a condição é verdadeira."),
               SlotDef(id="nao", label="Se não", empty_hint="Adicione os passos que rodam quando a condição é falsa.")],
        outputs=[PortDef(id="resultado", label="Resultado do teste", type="booleano",
                         description="Sim ou não, depois que a condição é avaliada")],
        params=[_param_regras(), _param_combinador()])

    para_cada = BlockType(
        id="builtin.para_cada", version=VERSAO, name="Para cada", kind="builtin", category="Controle", icon="loop",
        description="Repete os passos de dentro para cada item de uma lista. Há um limite de itens.",
        slots=[SlotDef(id="corpo", label="Para cada item", empty_hint="Adicione os passos a repetir para cada item.")],
        inputs=[PortDef(id="lista", label="Lista", type="lista", description="A lista a percorrer")],
        outputs=[PortDef(id="item", label="Item atual", type="qualquer", inside=True,
                         description="O item da vez (só dentro do bloco)"),
                 PortDef(id="indice", label="Posição do item", type="numero", inside=True,
                         description="0 para o primeiro item, 1 para o segundo… (só dentro do bloco)"),
                 PortDef(id="quantidade", label="Quantidade de itens", type="numero")],
        params=[ParamDef(id="limite", label="Limite de itens", type="numero", required=True, default=100, min=1, max=10000,
                         help="Se a lista tiver mais itens que o limite, o bloco falha em vez de cortar a lista em silêncio.")])

    repetir_ate = BlockType(
        id="builtin.repetir_ate", version=VERSAO, name="Repetir até", kind="builtin", category="Controle", icon="loop",
        description="Repete os passos de dentro até a condição ficar verdadeira. Falha se passar do limite de repetições.",
        slots=[SlotDef(id="corpo", label="Repetir", empty_hint="Adicione os passos a repetir.")],
        outputs=[PortDef(id="indice", label="Repetição atual", type="numero", inside=True,
                         description="0 na primeira repetição (só dentro do bloco)"),
                 PortDef(id="repeticoes", label="Repetições feitas", type="numero")],
        params=[_param_regras(), _param_combinador(),
                ParamDef(id="limite", label="Limite de repetições", type="numero", required=True, default=10, min=1, max=100,
                         help="Se a condição não ficar verdadeira dentro do limite, o bloco falha.")])

    escopo = BlockType(
        id="builtin.escopo", version=VERSAO, name="Escopo", kind="builtin", category="Controle", icon="scope",
        description="Agrupa passos. Use um escopo como “Tentar” e outro passo com “Executar após: falhou” como “Capturar erro”.",
        slots=[SlotDef(id="corpo", label="Passos do escopo", transparent=True,
                       empty_hint="Adicione os passos que fazem parte deste grupo.")],
        outputs=[PortDef(id="falhou", label="Algum passo falhou?", type="booleano"),
                 PortDef(id="erro", label="Mensagem do erro", type="texto",
                         description="Vazio se nada falhou; senão, o erro do primeiro passo que falhou"),
                 PortDef(id="resultados", label="Resultado de cada passo", type="lista",
                         description="Lista com nome, estado e erro de cada passo de dentro")])

    encerrar = BlockType(
        id="builtin.encerrar", version=VERSAO, name="Encerrar", kind="builtin", category="Controle", icon="stop",
        description="Para o fluxo na hora e define como a execução termina: sucesso, falha ou cancelada.",
        inputs=[PortDef(id="mensagem", label="Mensagem", type="texto", required=False,
                        description="Aparece no resultado da execução")],
        params=[ParamDef(id="estado", label="Terminar como", type="selecao", required=True, default="falha",
                         options=_opcoes(("sucesso", "Sucesso"), ("falha", "Falha"), ("cancelado", "Cancelado")))])

    var_inicializar = BlockType(
        id="builtin.var_inicializar", version=VERSAO, name="Inicializar variável", kind="builtin", category="Variáveis",
        icon="var",
        description="Cria uma variável para guardar um valor que muda durante o fluxo. Só pode ficar na lista principal.",
        inputs=[PortDef(id="inicial", label="Valor inicial", type="qualquer", required=False,
                        type_from=TypeFrom(param="tipo"), description="Se ficar vazio, começa com o valor neutro do tipo")],
        outputs=[PortDef(id="valor", label="Valor da variável", type="qualquer", type_from=TypeFrom(param="tipo"),
                         description="O valor que a variável tem naquele momento do fluxo")],
        params=[ParamDef(id="nome", label="Nome", type="texto", required=True, placeholder="total"),
                ParamDef(id="tipo", label="Tipo", type="selecao", required=True, default="texto", options=TIPOS_VARIAVEL)])

    var_definir = BlockType(
        id="builtin.var_definir", version=VERSAO, name="Definir variável", kind="builtin", category="Variáveis", icon="var",
        description="Troca o valor de uma variável.",
        inputs=[PortDef(id="valor", label="Novo valor", type="qualquer")],
        outputs=[PortDef(id="valor", label="Valor da variável", type="qualquer")],
        params=[_param_variavel()])

    var_incrementar = BlockType(
        id="builtin.var_incrementar", version=VERSAO, name="Incrementar variável", kind="builtin", category="Variáveis",
        icon="var", description="Soma um número ao valor de uma variável numérica (use um número negativo para diminuir).",
        inputs=[PortDef(id="quantidade", label="Quantidade", type="numero", required=False, default=1)],
        outputs=[PortDef(id="valor", label="Valor da variável", type="numero")],
        params=[_param_variavel()])

    var_acrescentar = BlockType(
        id="builtin.var_acrescentar", version=VERSAO, name="Acrescentar à lista", kind="builtin", category="Variáveis",
        icon="var", description="Coloca um valor no fim de uma variável do tipo lista.",
        inputs=[PortDef(id="valor", label="Valor a acrescentar", type="qualquer")],
        outputs=[PortDef(id="valor", label="Valor da variável", type="lista")],
        params=[_param_variavel()])

    compor = BlockType(
        id="builtin.compor", version=VERSAO, name="Compor", kind="builtin", category="Dados", icon="value",
        description="Guarda um valor (um texto montado com conteúdo dinâmico, um número, uma lista…) para usar nos próximos passos.",
        inputs=[PortDef(id="entrada", label="Entrada", type="qualquer")],
        outputs=[PortDef(id="resultado", label="Resultado", type="qualquer", type_from=TypeFrom(input="entrada"))])

    selecionar = BlockType(
        id="builtin.selecionar_campos", version=VERSAO, name="Selecionar campos", kind="builtin", category="Dados", icon="pick",
        description="Extrai campos de um objeto JSON. Use ponto para campos internos (endereco.cidade) e números para listas (itens.0).",
        inputs=[PortDef(id="objeto", label="Objeto", type="json")],
        outputs=[PortDef(id="valor", label="Valor", type="qualquer", description="Valor do primeiro campo da lista"),
                 PortDef(id="selecionados", label="Selecionados", type="json", description="Objeto com todos os campos escolhidos")],
        params=[
            ParamDef(id="caminhos", label="Campos", type="texto", required=True, multiline=True,
                     placeholder="nome", help="Um campo por linha. Ex.: nome ou endereco.cidade"),
            ParamDef(id="se_ausente", label="Se o campo não existir", type="selecao", default="erro",
                     options=_opcoes(("erro", "Mostrar erro"), ("nulo", "Usar vazio (nulo)"))),
        ])

    transformar = BlockType(
        id="builtin.transformar_lista", version=VERSAO, name="Transformar lista", kind="builtin", category="Dados", icon="loop",
        description="Aplica a mesma transformação a todos os itens de uma lista e devolve a nova lista (com Python, tudo em uma só execução).",
        inputs=[PortDef(id="lista", label="Lista", type="lista")],
        outputs=[PortDef(id="resultado", label="Nova lista", type="lista"),
                 PortDef(id="quantidade", label="Quantidade", type="numero")],
        params=[
            ParamDef(id="operacao", label="Transformação", type="selecao", required=True, default="multiplicar",
                     options=_opcoes(("multiplicar", "Multiplicar por um número"), ("somar", "Somar um número"),
                                     ("subtrair", "Subtrair um número"), ("dividir", "Dividir por um número"),
                                     ("potencia", "Elevar a um expoente"), ("maiusculas", "Texto em MAIÚSCULAS"),
                                     ("minusculas", "Texto em minúsculas"), ("remover_espacos", "Remover espaços das pontas"),
                                     ("adicionar_texto", "Adicionar texto antes/depois"), ("para_texto", "Converter para texto"),
                                     ("para_numero", "Converter para número"), ("extrair_campo", "Extrair um campo de cada objeto"),
                                     ("python", "Código Python (avançado)"))),
            ParamDef(id="operando", label="Número", type="numero", required=True, default=2,
                     visible_when=VisibleWhen(param="operacao", values=OPERACOES_NUMERICAS)),
            ParamDef(id="prefixo", label="Antes", type="texto", allow_empty=True,
                     visible_when=VisibleWhen(param="operacao", values=["adicionar_texto"])),
            ParamDef(id="sufixo", label="Depois", type="texto", allow_empty=True,
                     visible_when=VisibleWhen(param="operacao", values=["adicionar_texto"])),
            ParamDef(id="campo", label="Campo", type="texto", required=True, placeholder="nome",
                     visible_when=VisibleWhen(param="operacao", values=["extrair_campo"]),
                     help="Use ponto para campos internos. Ex.: endereco.cidade"),
            ParamDef(id="codigo", label="Código Python", type="codigo", required=True, default=CODIGO_TRANSFORMAR, multiline=True,
                     visible_when=VisibleWhen(param="operacao", values=["python"]),
                     help="Defina transformar(item). Roda no executor isolado, uma vez para cada item."),
            ParamDef(id="limite", label="Limite de itens", type="numero", required=True, default=100, min=1, max=10000,
                     help="Se a lista tiver mais itens que o limite, o bloco falha em vez de cortar a lista em silêncio."),
        ])

    texto = BlockType(
        id="builtin.texto", version=VERSAO, name="Transformar texto", kind="builtin", category="Texto", icon="text",
        description="Muda um texto: maiúsculas, minúsculas, remover espaços, substituir, juntar e mais.",
        inputs=[PortDef(id="texto", label="Texto", type="texto"),
                PortDef(id="outro", label="Outro texto", type="texto", required=False,
                        description="Usado só na operação “Juntar com outro texto”")],
        outputs=[PortDef(id="resultado", label="Resultado", type="texto"),
                 PortDef(id="tamanho", label="Tamanho", type="numero", description="Quantidade de caracteres do resultado")],
        params=[
            ParamDef(id="operacao", label="Operação", type="selecao", required=True, default="maiusculas",
                     options=_opcoes(("maiusculas", "MAIÚSCULAS"), ("minusculas", "minúsculas"),
                                     ("titulo", "Primeira Letra Maiúscula"), ("remover_espacos", "Remover espaços das pontas"),
                                     ("inverter", "Inverter o texto"), ("substituir", "Substituir um trecho"),
                                     ("prefixo_sufixo", "Adicionar antes e depois"),
                                     ("concatenar", "Juntar com outro texto"))),
            ParamDef(id="buscar", label="Trecho a procurar", type="texto", required=True,
                     visible_when=VisibleWhen(param="operacao", values=["substituir"])),
            ParamDef(id="substituir_por", label="Trocar por", type="texto", allow_empty=True,
                     visible_when=VisibleWhen(param="operacao", values=["substituir"])),
            ParamDef(id="prefixo", label="Antes do texto", type="texto", allow_empty=True,
                     visible_when=VisibleWhen(param="operacao", values=["prefixo_sufixo"])),
            ParamDef(id="sufixo", label="Depois do texto", type="texto", allow_empty=True,
                     visible_when=VisibleWhen(param="operacao", values=["prefixo_sufixo"])),
            ParamDef(id="separador", label="Separador", type="texto", allow_empty=True, default=" ",
                     visible_when=VisibleWhen(param="operacao", values=["concatenar"]),
                     help="Texto colocado entre os dois textos (ex.: um espaço)."),
        ])

    matematica = BlockType(
        id="builtin.matematica", version=VERSAO, name="Operação matemática", kind="builtin", category="Cálculo", icon="calc",
        description="Faz uma conta entre dois números: somar, subtrair, multiplicar, dividir e outras.",
        inputs=[PortDef(id="a", label="A", type="numero"), PortDef(id="b", label="B", type="numero")],
        outputs=[PortDef(id="resultado", label="Resultado", type="numero")],
        params=[ParamDef(id="operacao", label="Operação", type="selecao", required=True, default="somar",
                         options=_opcoes(("somar", "Somar (A + B)"), ("subtrair", "Subtrair (A − B)"),
                                         ("multiplicar", "Multiplicar (A × B)"), ("dividir", "Dividir (A ÷ B)"),
                                         ("resto", "Resto da divisão"), ("potencia", "Potência (A elevado a B)"),
                                         ("minimo", "Menor dos dois"), ("maximo", "Maior dos dois")))])

    python = BlockType(
        id="builtin.python", version=VERSAO, name="Executar código Python", kind="builtin", category="Python", icon="python",
        description="Escreva Python direto no fluxo: declare as entradas e saídas e programe a lógica. Roda em contêiner isolado, sem rede.",
        inputs_from="entradas", outputs_from="saidas",
        params=[ParamDef(id="entradas", label="Entradas", type="portas", default=ENTRADAS_PYTHON,
                         help="Os campos que o código recebe em inputs. Preencha cada um com valor fixo ou conteúdo dinâmico."),
                ParamDef(id="saidas", label="Saídas", type="portas", default=SAIDAS_PYTHON,
                         help="As chaves do dicionário que run() devolve; viram conteúdo dinâmico para os próximos passos."),
                ParamDef(id="codigo", label="Código", type="codigo", required=True, default=CODIGO_PYTHON, multiline=True)])

    saida = BlockType(
        id="builtin.saida", version=VERSAO, name="Saída final", kind="builtin", category="Saída", icon="flag",
        description="Mostra um resultado do fluxo na área de resultados do teste.",
        inputs=[PortDef(id="valor", label="Valor", type="qualquer")],
        params=[ParamDef(id="titulo", label="Título do resultado", type="texto", default="Resultado", allow_empty=True)])

    return [
        (gatilho, _gatilho, None),
        (condicao, None, None),
        (para_cada, None, None),
        (repetir_ate, None, None),
        (escopo, None, None),
        (encerrar, None, None),
        (var_inicializar, None, _validar_variavel_nova),
        (var_definir, None, None),
        (var_incrementar, None, None),
        (var_acrescentar, None, None),
        (compor, _compor, None),
        (selecionar, _selecionar_campos, None),
        (transformar, _transformar_lista, _validar_transformar_lista),
        (texto, _texto, None),
        (matematica, _matematica, None),
        (python, None, None),
        (saida, _saida, None),
    ]


_DEFS = _definicoes()
TIPOS_INTERNOS: dict[tuple[str, int], BlockType] = {(t.id, t.version): t for t, _, _ in _DEFS}
HANDLERS: dict[str, Handler] = {t.id: h for t, h, _ in _DEFS if h}
VALIDADORES: dict[str, Validador] = {t.id: v for t, _, v in _DEFS if v}


def requer_sandbox(tipo: BlockType, params: dict[str, Any]) -> bool:
    """Este passo, com esta configuração, executa código do usuário?"""
    if tipo.kind == "python" or tipo.id == "builtin.python":
        return True
    return tipo.id == "builtin.transformar_lista" and params.get("operacao") == "python"
