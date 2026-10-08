"""Blocos internos da biblioteca inicial.

Estes blocos são código NOSSO e rodam no processo da API. Por isso são deliberadamente
limitados: não avaliam expressões nem expressões regulares fornecidas pelo usuário e
limitam o tamanho das contas. Qualquer coisa escrita pelo usuário em Python roda
exclusivamente no executor isolado.
"""

from __future__ import annotations

import json
import math
import string
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import Limites
from ..errors import ErroBloco
from ..models import BlockType, Option, ParamDef, PortDef, TypeFrom, VisibleWhen
from ..tipos import descrever_valor, tipo_do_valor

# Cada bloco tem uma versão de contrato; mudar o contrato exige nova versão.
VERSAO = 1


@dataclass
class ContextoBloco:
    """O que um bloco interno pode usar além de suas entradas e parâmetros."""

    limites: Limites
    dados_iniciais: dict[str, Any] | None = None
    # (código, itens, params) -> lista transformada; levanta ErroBloco.
    mapa_python: Callable[[str, list, dict], list] | None = None
    # Logs produzidos durante o bloco (preenchido pelo motor, ex.: saída do código Python).
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


def _texto_de(v: Any) -> str:
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


# ------------------------------------------------------------------------------ handlers
def _inicio(inputs, params, ctx):
    dados = ctx.dados_iniciais if ctx.dados_iniciais is not None else params.get("dados", {})
    if not isinstance(dados, dict):
        raise ErroBloco("Os dados de entrada do início precisam ser um objeto JSON, como {\"nome\": \"Ana\"}.",
                        codigo="dados_invalidos")
    return {"dados": dados}


def _constante(inputs, params, ctx):
    return {"valor": params["valor"]}


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
                        sugestao="Confira o valor conectado à entrada B: ele não pode ser 0.")
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
        r = t.replace(params["buscar"], params.get("substituir_por", ""))
    elif op == "prefixo_sufixo":
        r = f"{params.get('prefixo', '')}{t}{params.get('sufixo', '')}"
    elif op == "concatenar":
        if "outro" not in inputs:
            raise ErroBloco("Para juntar textos, conecte um texto à entrada “Outro texto”.",
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
    nulo = params.get("se_ausente", "erro") == "nulo"
    selecionados: dict[str, Any] = {}
    for caminho in caminhos:
        try:
            selecionados[caminho] = buscar_caminho(objeto, caminho)
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


def _converter_comparacao(bruto: str, tipo: str, valor: Any) -> Any:
    if tipo == "automatico":
        tv = tipo_do_valor(valor)
        tipo = tv if tv in ("numero", "booleano") else "texto"
    if tipo == "texto":
        return bruto
    if tipo == "numero":
        n = _parse_numero(bruto)
        if n is None:
            raise ErroBloco(f"“{bruto}” não é um número válido para comparar.", codigo="parametro_invalido",
                            sugestao="Digite apenas o número, por exemplo 10 ou 2,5.")
        return n
    t = bruto.strip().lower()
    if t in ("true", "verdadeiro", "sim", "1"):
        return True
    if t in ("false", "falso", "nao", "não", "0"):
        return False
    raise ErroBloco(f"“{bruto}” não é um valor booleano válido.", codigo="parametro_invalido",
                    sugestao="Use verdadeiro/falso (ou sim/não).")


def _condicao(inputs, params, ctx):
    valor = inputs["valor"]
    op = params.get("operador", "igual")
    bruto = str(params.get("comparar_com", ""))
    tipo_cmp = params.get("tipo_comparacao", "automatico")

    if op in ("vazio", "nao_vazio"):
        vazio = valor is None or valor == [] or valor == {} or (isinstance(valor, str) and valor.strip() == "")
        r = vazio if op == "vazio" else not vazio
    elif op in ("verdadeiro", "falso"):
        if not isinstance(valor, bool):
            raise ErroBloco(
                f"Esta condição espera um valor sim/não, mas recebeu {descrever_valor(valor)}.",
                codigo="tipo_invalido", sugestao="Use outro operador (ex.: “é igual a”) ou conecte um valor booleano.")
        r = valor if op == "verdadeiro" else not valor
    elif op in ("contem", "nao_contem"):
        if isinstance(valor, str):
            achou = bruto in valor
        elif isinstance(valor, list):
            candidatos: list[Any] = [bruto]
            n = _parse_numero(bruto)
            if n is not None:
                candidatos.append(n)
            achou = any(_igual(item, c) for item in valor for c in candidatos)
        elif isinstance(valor, dict):
            achou = bruto in valor
        else:
            raise ErroBloco(f"“contém” só funciona com texto, lista ou objeto, mas recebeu {descrever_valor(valor)}.",
                            codigo="tipo_invalido")
        r = achou if op == "contem" else not achou
    else:
        ref = _converter_comparacao(bruto, tipo_cmp, valor)
        if op in ("igual", "diferente"):
            r = _igual(valor, ref)
            if op == "diferente":
                r = not r
        else:
            if _eh_numero(valor) and _eh_numero(ref):
                pass
            elif isinstance(valor, str) and isinstance(ref, str):
                pass
            else:
                raise ErroBloco(
                    f"Não é possível comparar {descrever_valor(valor)} com {descrever_valor(ref)}.",
                    codigo="tipo_invalido",
                    sugestao="Ajuste o tipo da comparação ou o valor conectado para que ambos sejam números (ou ambos textos).")
            r = {"maior": valor > ref, "maior_igual": valor >= ref,
                 "menor": valor < ref, "menor_igual": valor <= ref}[op]
    saidas: dict[str, Any] = {"resultado": r}
    saidas["verdadeiro" if r else "falso"] = valor  # a outra porta fica inativa (não é devolvida)
    return saidas


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
        return f"{params.get('prefixo', '')}{_texto_de(item)}{params.get('sufixo', '')}"
    if op == "para_texto":
        return _texto_de(item)
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


def _para_cada(inputs, params, ctx):
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
        resultado = [_aplicar_item(op, item, i, params) for i, item in enumerate(lista)]
    return {"resultado": resultado, "quantidade": len(resultado)}


def _saida(inputs, params, ctx):
    return {}


# ------------------------------------------------------------------------------ validadores
def _validar_matematica(params):
    return []


def _validar_para_cada(params):
    erros: list[tuple[str | None, str]] = []
    if params.get("operacao") == "dividir" and params.get("operando", 1) == 0:
        erros.append(("operando", "Não é possível dividir por zero: escolha um valor diferente de 0."))
    return erros


def _validar_condicao(params):
    erros: list[tuple[str | None, str]] = []
    if params.get("operador") in ("igual", "diferente", "maior", "maior_igual", "menor", "menor_igual") \
            and params.get("tipo_comparacao") == "numero" and _parse_numero(str(params.get("comparar_com", ""))) is None:
        erros.append(("comparar_com", "Digite um número válido para comparar (ex.: 10 ou 2,5)."))
    return erros


# ------------------------------------------------------------------------------ definições
CODIGO_PARA_CADA = (
    "def transformar(item):\n"
    "    # Recebe cada item da lista e devolve o novo valor.\n"
    "    return item\n"
)

OPERACOES_NUMERICAS = ["multiplicar", "somar", "subtrair", "dividir", "potencia"]


def _definicoes() -> list[tuple[BlockType, Handler, Validador | None]]:
    inicio = BlockType(
        id="builtin.inicio", version=VERSAO, name="Início manual", kind="builtin", category="Entrada e saída", icon="play",
        description="Começa o fluxo com dados que você informa. Os dados saem como um objeto JSON.",
        outputs=[PortDef(id="dados", label="Dados", type="json", description="Objeto JSON com os dados de entrada")],
        params=[ParamDef(id="dados", label="Dados de entrada", type="json", required=True, default={},
                         multiline=True, help='Objeto JSON entregue ao fluxo. Exemplo: {"nome": "Ana"}')])

    constante = BlockType(
        id="builtin.constante", version=VERSAO, name="Valor constante", kind="builtin", category="Dados", icon="value",
        description="Entrega sempre o mesmo valor: um texto, número, sim/não, lista ou objeto JSON.",
        outputs=[PortDef(id="valor", label="Valor", type="qualquer", type_from=TypeFrom(param="tipo"))],
        params=[
            ParamDef(id="tipo", label="Tipo do valor", type="selecao", required=True, default="texto",
                     options=_opcoes(("texto", "Texto"), ("numero", "Número"), ("booleano", "Sim/Não"),
                                     ("lista", "Lista"), ("json", "Objeto JSON"))),
            ParamDef(id="valor", label="Valor", type="texto", required=True, allow_empty=True, default="",
                     type_from=TypeFrom(param="tipo"), help="O formato do campo muda conforme o tipo escolhido."),
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

    condicao = BlockType(
        id="builtin.condicao", version=VERSAO, name="Condição (se / senão)", kind="builtin", category="Controle de fluxo", icon="branch",
        description="Testa um valor e segue por um de dois caminhos: “verdadeiro” ou “falso”. Só o caminho escolhido é executado.",
        inputs=[PortDef(id="valor", label="Valor", type="qualquer")],
        outputs=[PortDef(id="resultado", label="Resultado do teste", type="booleano", description="Sempre disponível: sim ou não"),
                 PortDef(id="verdadeiro", label="Se verdadeiro", type="qualquer", type_from=TypeFrom(input="valor"), conditional=True,
                         description="Segue o valor recebido quando o teste é verdadeiro"),
                 PortDef(id="falso", label="Se falso", type="qualquer", type_from=TypeFrom(input="valor"), conditional=True,
                         description="Segue o valor recebido quando o teste é falso")],
        params=[
            ParamDef(id="operador", label="Teste", type="selecao", required=True, default="igual",
                     options=_opcoes(("igual", "é igual a"), ("diferente", "é diferente de"), ("maior", "é maior que"),
                                     ("maior_igual", "é maior ou igual a"), ("menor", "é menor que"),
                                     ("menor_igual", "é menor ou igual a"), ("contem", "contém"), ("nao_contem", "não contém"),
                                     ("vazio", "está vazio"), ("nao_vazio", "não está vazio"),
                                     ("verdadeiro", "é verdadeiro (sim)"), ("falso", "é falso (não)"))),
            ParamDef(id="comparar_com", label="Comparar com", type="texto", allow_empty=True,
                     visible_when=VisibleWhen(param="operador", values=["igual", "diferente", "maior", "maior_igual",
                                                                        "menor", "menor_igual", "contem", "nao_contem"])),
            ParamDef(id="tipo_comparacao", label="Tratar o valor acima como", type="selecao", default="automatico",
                     options=_opcoes(("automatico", "Automático (mesmo tipo do valor recebido)"), ("texto", "Texto"),
                                     ("numero", "Número"), ("booleano", "Sim/Não")),
                     visible_when=VisibleWhen(param="operador", values=["igual", "diferente", "maior", "maior_igual",
                                                                        "menor", "menor_igual"])),
        ])

    para_cada = BlockType(
        id="builtin.para_cada", version=VERSAO, name="Para cada item da lista", kind="builtin", category="Controle de fluxo", icon="loop",
        description="Aplica a mesma transformação a todos os itens de uma lista e devolve a nova lista. Há um limite de itens.",
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
            ParamDef(id="codigo", label="Código Python", type="codigo", required=True, default=CODIGO_PARA_CADA, multiline=True,
                     visible_when=VisibleWhen(param="operacao", values=["python"]),
                     help="Defina transformar(item). Roda no executor isolado, uma vez para cada item."),
            ParamDef(id="limite", label="Limite de itens", type="numero", required=True, default=100, min=1, max=10000,
                     help="Se a lista tiver mais itens que o limite, o bloco falha em vez de cortar a lista em silêncio."),
        ])

    saida = BlockType(
        id="builtin.saida", version=VERSAO, name="Saída final", kind="builtin", category="Entrada e saída", icon="flag",
        description="Mostra o resultado final do fluxo na área de resultados.",
        inputs=[PortDef(id="valor", label="Valor", type="qualquer")],
        params=[ParamDef(id="titulo", label="Título do resultado", type="texto", default="Resultado", allow_empty=True)])

    return [
        (inicio, _inicio, None),
        (constante, _constante, None),
        (matematica, _matematica, _validar_matematica),
        (texto, _texto, None),
        (selecionar, _selecionar_campos, None),
        (condicao, _condicao, _validar_condicao),
        (para_cada, _para_cada, _validar_para_cada),
        (saida, _saida, None),
    ]


_DEFS = _definicoes()
TIPOS_INTERNOS: dict[tuple[str, int], BlockType] = {(t.id, t.version): t for t, _, _ in _DEFS}
HANDLERS: dict[str, Handler] = {t.id: h for t, h, _ in _DEFS}
VALIDADORES: dict[str, Validador] = {t.id: v for t, _, v in _DEFS if v}


def requer_sandbox(tipo: BlockType, params: dict[str, Any]) -> bool:
    """Este bloco, com esta configuração, executa código do usuário?"""
    if tipo.kind == "python":
        return True
    return tipo.id == "builtin.para_cada" and params.get("operacao") == "python"
