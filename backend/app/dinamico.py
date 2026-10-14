"""Conteúdo dinâmico: o valor de um campo a partir do que os passos anteriores produziram, e as regras da condição.

O conteúdo dinâmico só LÊ valores (chaves de objeto e posições de lista, via ``buscar_caminho``): nunca avalia texto
nem código. Um campo montado com texto + referências vira um texto concatenado, com tamanho limitado.

Um campo com expressão Python chega aqui já calculado pelo executor isolado (``Calculadas``, pela chave do campo): este
módulo só diz quais campos têm expressão e devolve o valor que veio de lá. Testes como “está vazio” não usam o segundo
valor da regra, que então não é calculado nem lido.
"""

from __future__ import annotations

from typing import Any

from .blocks.builtin import OPERADORES_UNARIOS, avaliar_regra, buscar_caminho, combinar_regras, texto_de
from .config import Limites
from .errors import ErroBloco
from .erros_sandbox import resumo_da_expressao
from .execucao import Execucao
from .models import BlockType, Campo, Passo, Ref
from .passos import definicao_efetiva, regras_declaradas
from .tipos import descrever_valor, rotulo_tipo, valor_e_do_tipo
from .validation import campo_vazio, parametros_efetivos

Calculadas = dict[str, Any]  # valor de cada expressão do passo, pela chave do campo (``chave_entrada`` / ``chave_regra``)


def chave_entrada(porta_id: str) -> str:
    return f"entrada:{porta_id}"


def chave_regra(indice: int, lado: str) -> str:
    return f"regra:{indice}:{lado}"


def expressoes_do_passo(passo: Passo, tipo: BlockType, *, entradas: bool = True,
                        regras: bool = True) -> list[tuple[str, str, Campo]]:
    """Os campos do passo com expressão a calcular: (chave, rótulo do campo, campo), na ordem da tela. Expressões em
    branco ficam de fora (o campo conta como vazio)."""
    achadas: list[tuple[str, str, Campo]] = []
    if entradas:
        for porta in definicao_efetiva(tipo, passo.params).inputs:
            campo = passo.inputs.get(porta.id)
            if campo is not None and campo.expr is not None and not campo_vazio(campo, "qualquer"):
                achadas.append((chave_entrada(porta.id), porta.label, campo))
    if regras and any(pd.type == "regras" for pd in tipo.params):
        lidas, _ = regras_declaradas(parametros_efetivos(tipo, passo.params).get("regras"))
        for i, r in enumerate(lidas):
            onde = f"condição {i + 1}" if len(lidas) > 1 else "condição"
            lados: list[tuple[str, Campo | None, str]] = [("esq", r.esq, f"Valor da {onde}")]
            if r.op not in OPERADORES_UNARIOS:
                lados.append(("dir", r.dir, f"Comparar com ({onde})"))
            for lado, campo, rotulo in lados:
                if campo is not None and campo.expr is not None and not campo_vazio(campo, "qualquer"):
                    achadas.append((chave_regra(i, lado), rotulo, campo))
    return achadas


def valor_da_ref(ex: Execucao, ref: Ref) -> Any:
    saidas = ex.valores.get(ref.step)
    rotulo = f"{ex.nomes.get(ref.step, ref.step)} › {ref.output}"
    if saidas is None or ref.output not in saidas:
        raise ErroBloco(
            f"O conteúdo dinâmico “{rotulo}” não está disponível: o passo não foi executado ou não produziu esse valor.",
            codigo="conteudo_indisponivel",
            sugestao="Confira se o passo de origem roda antes deste e se não foi ignorado ou não falhou.")
    valor = saidas[ref.output]
    if ref.path:
        try:
            return buscar_caminho(valor, ref.path)
        except (KeyError, IndexError, ValueError, TypeError):
            raise ErroBloco(f"O campo “{ref.path}” não existe em “{rotulo}”.", codigo="campo_ausente",
                            sugestao="Confira o nome do campo (use ponto para campos internos, como endereco.cidade).") from None
    return valor


def resolver_campo(ex: Execucao, campo: Campo, limites: Limites, calculadas: Calculadas | None = None, chave: str = "") -> Any:
    if campo.expr is not None:
        if calculadas is None or chave not in calculadas:  # nunca avalia aqui: sem o valor do executor, é falha nossa
            raise ErroBloco("A expressão deste campo não foi calculada.", codigo="erro_interno")
        return calculadas[chave]
    if not campo.dinamico:
        return campo.value
    partes = campo.parts or []
    if len(partes) == 1 and isinstance(partes[0], Ref):
        return valor_da_ref(ex, partes[0])
    pedacos: list[str] = []
    tamanho = 0
    for parte in partes:
        texto = parte if isinstance(parte, str) else texto_de(valor_da_ref(ex, parte))
        tamanho += len(texto)
        if tamanho > limites.valor_max:
            raise ErroBloco(f"O texto montado ficaria grande demais (mais de {limites.valor_max // 1024} KB).",
                            codigo="valor_grande_demais")
        pedacos.append(texto)
    return "".join(pedacos)


def montar_entradas(ex: Execucao, passo: Passo, ef: BlockType, limites: Limites,
                    calculadas: Calculadas | None = None) -> dict[str, Any]:
    entradas: dict[str, Any] = {}
    tipos = ex.port_types.get(passo.id, {}).get("inputs", {})
    for porta in ef.inputs:
        esperado = tipos.get(porta.id, porta.type)
        campo = passo.inputs.get(porta.id)
        if campo is None or campo_vazio(campo, esperado):
            if porta.default is not None:
                entradas[porta.id] = porta.default
            elif porta.required:
                raise ErroBloco(f"O campo obrigatório “{porta.label}” não foi preenchido.", codigo="entrada_ausente",
                                sugestao="Preencha o campo com um valor ou com um conteúdo dinâmico.")
            continue
        valor = resolver_campo(ex, campo, limites, calculadas, chave_entrada(porta.id))
        if not valor_e_do_tipo(valor, esperado):
            if campo.expr is not None:
                raise ErroBloco(
                    f"A expressão do campo “{porta.label}” (`{resumo_da_expressao(campo.expr)}`) devolveu "
                    f"{descrever_valor(valor)}, mas o campo espera {rotulo_tipo(esperado)}.",
                    codigo="entrada_invalida",
                    sugestao="Ajuste a expressão para devolver o tipo certo, por exemplo str(...) para texto ou float(...) para número.")
            raise ErroBloco(
                f"O campo “{porta.label}” esperava {rotulo_tipo(esperado)}, mas recebeu {descrever_valor(valor)}.",
                codigo="entrada_invalida", sugestao="Confira o conteúdo dinâmico ou o valor digitado neste campo.")
        entradas[porta.id] = valor
    return entradas


def avaliar_regras(ex: Execucao, params: dict[str, Any], limites: Limites, calculadas: Calculadas | None = None) -> bool:
    regras, erro = regras_declaradas(params.get("regras"))
    if erro:
        raise ErroBloco(erro, codigo="parametro_invalido")
    resultados = []
    for i, r in enumerate(regras):
        esq = resolver_campo(ex, r.esq, limites, calculadas, chave_regra(i, "esq"))
        dir_ = (resolver_campo(ex, r.dir, limites, calculadas, chave_regra(i, "dir"))
                if r.dir is not None and r.op not in OPERADORES_UNARIOS else None)
        resultados.append(avaliar_regra(esq, r.op, dir_))
    return combinar_regras(str(params.get("combinador", "e")), resultados)
