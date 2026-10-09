"""Tradução dos erros do executor isolado para mensagens de iniciante + detalhes técnicos.

O runner roda dentro do contêiner e sua resposta é tratada como não confiável: aqui ela só vira texto e códigos
conhecidos, sem nunca ser avaliada.
"""

from __future__ import annotations

from typing import Any

from .errors import ErroBloco

_AMIGAVEIS = {
    "ZeroDivisionError": "O código tentou dividir por zero.",
    "KeyError": "O código procurou uma chave que não existe: {m}.",
    "NameError": "O código usa um nome que não foi definido: {m}.",
    "UnboundLocalError": "O código usa uma variável antes de ela receber um valor: {m}.",
    "TypeError": "O código misturou valores de tipos que não combinam ({m}).",
    "ValueError": "O código recebeu um valor que não consegue usar ({m}).",
    "IndexError": "O código tentou acessar uma posição que não existe ({m}).",
    "AttributeError": "O código tentou usar algo que esse valor não possui ({m}).",
    "ImportError": "O código tentou usar uma biblioteca que não está disponível ({m}).",
    "ModuleNotFoundError": "O código tentou usar uma biblioteca que não está disponível ({m}).",
    "RecursionError": "O código chamou a si mesmo vezes demais (recursão sem fim).",
    "IndentationError": "A indentação (os espaços no começo das linhas) está incorreta.",
    "AssertionError": "Uma verificação (assert) do código falhou.",
}

_CODIGOS = {"excecao": "excecao_python", "sintaxe": "sintaxe_python"}

SUGESTOES = {
    "excecao": "Revise o código do passo. Os logs e os detalhes técnicos mostram onde ele parou.",
    "sintaxe": "Confira parênteses, dois-pontos (:) e a indentação perto da linha indicada.",
    "tempo_esgotado": "Procure laços sem fim (como `while True`) ou reduza a quantidade de dados processados.",
    "memoria_excedida": "Evite criar listas ou textos enormes; processe menos dados de uma vez.",
    "saida_excessiva": "Reduza o uso de print() e o tamanho do que o código imprime.",
    "retorno_invalido": "Devolva um dicionário com exatamente as saídas declaradas, por exemplo: return {\"mensagem\": texto}.",
    "funcao_ausente": "O código precisa ter `def run(inputs, params):` e devolver um dicionário com as saídas.",
}

# Falhas que repetir não resolve (configuração ou dados errados).
NAO_REPETIR = frozenset({"entrada_invalida", "entrada_ausente", "executor_indisponivel", "conteudo_indisponivel",
                         "parametro_invalido", "limite_itens", "registros_demais", "tipo_invalido"})


def erro_da_sandbox(err: dict[str, Any], quando: str = "") -> ErroBloco:
    """Traduz o erro do executor isolado em uma mensagem para iniciantes + detalhes técnicos."""
    categoria = err.get("category", "erro_interno")
    tipo = err.get("type", "")
    bruto = str(err.get("message", ""))
    linha = err.get("line")
    trecho = err.get("snippet")
    onde = f" (linha {linha}: `{trecho}`)" if linha and trecho else (f" (linha {linha})" if linha else "")
    if categoria == "excecao":
        base = _AMIGAVEIS.get(tipo, f"O código gerou um erro do tipo {tipo or 'desconhecido'}.").replace("{m}", bruto)
        mensagem = base.rstrip(".") + onde + "."
        if err.get("item_index") is not None:
            mensagem = f"No item {err['item_index'] + 1} da lista: " + mensagem[0].lower() + mensagem[1:]
    elif categoria == "sintaxe":
        mensagem = f"Há um erro de escrita (sintaxe) no código{onde}: {bruto}"
    elif categoria == "tempo_esgotado":
        mensagem = bruto.rstrip(".") + (f" (o código estava na linha {linha}: `{trecho}`)." if linha and trecho else ".")
    elif categoria in ("memoria_excedida", "saida_excessiva"):
        mensagem = bruto + (f" (linha {linha})" if linha else "")
    elif categoria in ("retorno_invalido", "funcao_ausente", "executor_indisponivel", "valor_grande_demais"):
        mensagem = bruto
    else:
        mensagem = bruto or "Ocorreu um erro inesperado ao executar o código."
    tecnico = {k: err.get(k) for k in ("type", "message", "line", "snippet", "traceback", "item_index") if err.get(k) not in (None, "")}
    return ErroBloco(mensagem, codigo=_CODIGOS.get(categoria, categoria),
                     sugestao=err.get("suggestion") or SUGESTOES.get(categoria), tecnico=tecnico or None)
