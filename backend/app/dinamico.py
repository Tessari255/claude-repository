"""Conteúdo dinâmico: o valor de um campo a partir do que os passos anteriores produziram, e as regras da condição.

O conteúdo dinâmico só LÊ valores (chaves de objeto e posições de lista, via ``buscar_caminho``): nunca avalia texto
nem código. Um campo montado com texto + referências vira um texto concatenado, com tamanho limitado.
"""

from __future__ import annotations

from typing import Any

from .blocks.builtin import avaliar_regra, buscar_caminho, combinar_regras, texto_de
from .config import Limites
from .errors import ErroBloco
from .execucao import Execucao
from .models import BlockType, Campo, Passo, Ref
from .passos import regras_declaradas
from .tipos import descrever_valor, rotulo_tipo, valor_e_do_tipo
from .validation import campo_vazio


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


def resolver_campo(ex: Execucao, campo: Campo, limites: Limites) -> Any:
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


def montar_entradas(ex: Execucao, passo: Passo, ef: BlockType, limites: Limites) -> dict[str, Any]:
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
        valor = resolver_campo(ex, campo, limites)
        if not valor_e_do_tipo(valor, esperado):
            raise ErroBloco(
                f"O campo “{porta.label}” esperava {rotulo_tipo(esperado)}, mas recebeu {descrever_valor(valor)}.",
                codigo="entrada_invalida", sugestao="Confira o conteúdo dinâmico ou o valor digitado neste campo.")
        entradas[porta.id] = valor
    return entradas


def avaliar_regras(ex: Execucao, params: dict[str, Any], limites: Limites) -> bool:
    regras, erro = regras_declaradas(params.get("regras"))
    if erro:
        raise ErroBloco(erro, codigo="parametro_invalido")
    resultados = []
    for r in regras:
        esq = resolver_campo(ex, r.esq, limites)
        dir_ = resolver_campo(ex, r.dir, limites) if r.dir is not None else None
        resultados.append(avaliar_regra(esq, r.op, dir_))
    return combinar_regras(str(params.get("combinador", "e")), resultados)
