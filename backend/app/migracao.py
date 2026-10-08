"""Migração de fluxos do formato 1 (grafo de blocos ligados por portas) para o formato 2 (passos em sequência).

O formato 1 era um grafo: blocos soltos na tela, ligados por conexões de portas. O formato 2 é uma lista de
passos com conteúdo dinâmico, como no Power Automate. A conversão preserva o comportamento:

* os blocos viram passos na **ordem topológica** (a mesma ordem em que o motor antigo os executava);
* cada conexão ``origem → entrada`` vira uma referência de conteúdo dinâmico no campo da entrada;
* ``Início manual`` vira o gatilho (com um campo ``dados``, objeto JSON); ``Valor constante`` vira ``Compor``;
* ``Condição`` vira uma condição de verdade com dois caminhos: os blocos que dependiam da saída “verdadeiro”
  ficam em “Se sim” e os de “falso” em “Se não”. As saídas “verdadeiro/falso” repassavam o valor testado, então
  quem as usava passa a usar o valor original;
* ``Para cada item da lista`` (que era uma transformação em uma só passada) vira ``Transformar lista``.

A conversão é feita com dicionários (sem validar), porque os dados antigos podem estar incompletos; o resultado
passa pela validação normal do formato 2.
"""

from __future__ import annotations

from typing import Any

ID_GATILHO = "gatilho"
SLOTS_CONDICAO = {"verdadeiro": "sim", "falso": "nao"}
UNARIOS = {"vazio", "nao_vazio", "verdadeiro", "falso"}


def fluxo_v1(flow: Any) -> bool:
    return isinstance(flow, dict) and flow.get("schema_version", 1) == 1


def _ordem_topologica(ids: list[str], conexoes: list[dict[str, Any]]) -> list[str]:
    grau = {i: 0 for i in ids}
    saida_de: dict[str, list[str]] = {i: [] for i in ids}
    for c in conexoes:
        s, d = c["source"]["block"], c["target"]["block"]
        if s in grau and d in grau and s != d:
            grau[d] += 1
            saida_de[s].append(d)
    posicao = {i: n for n, i in enumerate(ids)}
    prontos = sorted([i for i in ids if grau[i] == 0], key=posicao.get)  # type: ignore[arg-type]
    resultado: list[str] = []
    while prontos:
        atual = prontos.pop(0)
        resultado.append(atual)
        for w in saida_de[atual]:
            grau[w] -= 1
            if grau[w] == 0:
                prontos.append(w)
        prontos.sort(key=posicao.get)  # type: ignore[arg-type]
    return resultado if len(resultado) == len(ids) else list(ids)  # ciclo (não deveria existir): mantém a ordem da lista


def _ref(step: str, output: str) -> dict[str, Any]:
    return {"parts": [{"step": step, "output": output, "path": ""}]}


def migrar_fluxo(flow: dict[str, Any]) -> dict[str, Any]:
    """Converte um fluxo do formato 1 em um dicionário do formato 2 (já no formato 2: devolve igual)."""
    if not fluxo_v1(flow):
        return flow
    blocos = [b for b in flow.get("blocks", []) if isinstance(b, dict) and isinstance(b.get("id"), str)]
    conexoes = [c for c in flow.get("connections", [])
                if isinstance(c, dict) and isinstance(c.get("source"), dict) and isinstance(c.get("target"), dict)
                and c["source"].get("block") and c["target"].get("block")]
    por_id = {b["id"]: b for b in blocos}
    conexoes = [c for c in conexoes if c["source"]["block"] in por_id and c["target"]["block"] in por_id]
    ordem = _ordem_topologica([b["id"] for b in blocos], conexoes)

    # --- ids: "gatilho" é reservado
    novo_id = {i: (f"{i}_passo" if i == ID_GATILHO else i) for i in por_id}
    inicios = [i for i in ordem if por_id[i].get("type") == "builtin.inicio"]
    id_gatilho = inicios[0] if inicios else None

    # --- para onde foi cada saída antiga: (bloco, porta) -> (passo novo, saída nova)
    destino_saida: dict[tuple[str, str], tuple[str, str]] = {}
    entrada_de: dict[tuple[str, str], tuple[str, str]] = {}
    for c in conexoes:
        entrada_de[(c["target"]["block"], c["target"].get("port", ""))] = (c["source"]["block"], c["source"].get("port", ""))

    def origem_final(bloco: str, porta: str, vistos: frozenset[tuple[str, str]] = frozenset()) -> tuple[str, str] | None:
        """Segue as saídas condicionais (que só repassavam o valor testado) até a origem real do dado."""
        chave = (bloco, porta)
        if chave in vistos:
            return None
        b = por_id.get(bloco)
        if b and b.get("type") == "builtin.condicao" and porta in SLOTS_CONDICAO:
            origem = entrada_de.get((bloco, "valor"))
            return origem_final(origem[0], origem[1], vistos | {chave}) if origem else None
        if chave in destino_saida:
            return destino_saida[chave]
        return (novo_id[bloco], porta) if bloco in novo_id else None

    for bid in ordem:
        tipo = por_id[bid].get("type")
        if tipo == "builtin.inicio":
            destino_saida[(bid, "dados")] = (ID_GATILHO, "dados") if bid == id_gatilho else (novo_id[bid], "resultado")
        elif tipo == "builtin.constante":
            destino_saida[(bid, "valor")] = (novo_id[bid], "resultado")

    # --- portão: de qual caminho de qual condição cada bloco depende
    portao: dict[str, tuple[str, str] | None] = {}
    for bid in ordem:
        encontrado: tuple[str, str] | None = None
        for c in conexoes:
            if c["target"]["block"] != bid:
                continue
            s, p = c["source"]["block"], c["source"].get("port", "")
            if por_id[s].get("type") == "builtin.condicao" and p in SLOTS_CONDICAO:
                encontrado = (novo_id[s], SLOTS_CONDICAO[p])
            elif portao.get(s):
                encontrado = portao[s]
            if encontrado:
                break
        portao[bid] = encontrado

    # --- conversão de cada bloco em um passo
    passos: dict[str, dict[str, Any]] = {}
    for bid in ordem:
        b = por_id[bid]
        tipo = b.get("type", "")
        params = dict(b.get("params") or {}) if isinstance(b.get("params"), dict) else {}
        passo: dict[str, Any] = {"id": novo_id[bid], "type": tipo, "version": b.get("version", 1),
                                 "label": b.get("label"), "inputs": {}, "params": params}

        def campo(porta: str) -> dict[str, Any] | None:
            origem = entrada_de.get((bid, porta))
            if origem is None:
                return None
            final = origem_final(*origem)
            return _ref(*final) if final else None

        if tipo == "builtin.inicio":
            if bid == id_gatilho:
                continue  # vira o gatilho, abaixo
            passo.update(type="builtin.compor", version=1, params={})
            passo["inputs"] = {"entrada": {"value": params.get("dados", {})}}
        elif tipo == "builtin.constante":
            passo.update(type="builtin.compor", version=1, params={})
            passo["inputs"] = {"entrada": {"value": params.get("valor", "")}}
        elif tipo == "builtin.condicao":
            esq = campo("valor") or {"value": ""}
            operador = params.get("operador", "igual")
            regra: dict[str, Any] = {"esq": esq, "op": operador}
            if operador not in UNARIOS:
                regra["dir"] = {"value": str(params.get("comparar_com", ""))}
            passo.update(version=1, params={"regras": [regra], "combinador": "e"})
            passo["slots"] = {"sim": [], "nao": []}
        elif tipo == "builtin.para_cada":
            passo.update(type="builtin.transformar_lista", version=1)
            c = campo("lista")
            if c:
                passo["inputs"]["lista"] = c
        else:
            for c in conexoes:
                if c["target"]["block"] == bid:
                    porta = c["target"].get("port", "")
                    cp = campo(porta)
                    if cp and porta:
                        passo["inputs"][porta] = cp
        passos[bid] = passo

    # --- gatilho
    campos = []
    if id_gatilho is not None:
        dados = (por_id[id_gatilho].get("params") or {}).get("dados", {})
        campos = [{"id": "dados", "label": "Dados", "type": "json", "required": False, "default": dados if isinstance(dados, dict) else {}}]
    gatilho = {"id": ID_GATILHO, "type": "builtin.gatilho_manual", "version": 1, "label": None,
               "inputs": {}, "params": {"campos": campos}}

    # --- posiciona cada passo na lista certa (principal ou um caminho de uma condição) e na ordem certa.
    # Um passo só enxerga o que roda ANTES dele; e tudo o que há dentro de uma condição roda junto com ela.
    # Por isso a ordem de cada lista respeita as dependências de todos os blocos contidos em cada membro.
    antigo_de = {p["id"]: bid for bid, p in passos.items()}
    antigo_de[ID_GATILHO] = id_gatilho or ""
    dependencias: dict[str, set[str]] = {bid: set() for bid in passos}
    for bid in passos:
        for (alvo, _porta), origem in entrada_de.items():
            if alvo != bid:
                continue
            final = origem_final(*origem)
            if final and antigo_de.get(final[0]) in passos:
                dependencias[bid].add(antigo_de[final[0]])
    base = {bid: n for n, bid in enumerate(ordem)}

    def membro_de(bid: str, portao_da_lista: tuple[str, str] | None) -> str | None:
        """O membro da lista (identificada pelo seu portão) que contém o bloco `bid`, direta ou indiretamente."""
        atual: str | None = bid
        while atual is not None:
            g = portao.get(atual)
            if g == portao_da_lista:
                return atual
            if g is None:
                return None
            atual = antigo_de.get(g[0])
        return None

    def construir_lista(portao_da_lista: tuple[str, str] | None) -> list[dict[str, Any]]:
        membros = [b for b in ordem if b in passos and portao.get(b) == portao_da_lista]
        antes: dict[str, set[str]] = {m: set() for m in membros}
        for bid in passos:
            dono = membro_de(bid, portao_da_lista)
            if dono is None:
                continue
            for dep in dependencias[bid]:
                origem_na_lista = membro_de(dep, portao_da_lista)
                if origem_na_lista and origem_na_lista != dono:
                    antes[dono].add(origem_na_lista)
        resultado: list[str] = []
        restantes = sorted(membros, key=base.get)  # type: ignore[arg-type]
        while restantes:
            pronto = next((m for m in restantes if antes[m] <= set(resultado)), None)
            if pronto is None:  # ciclo: mantém a ordem original
                pronto = restantes[0]
            resultado.append(pronto)
            restantes.remove(pronto)
        lista = []
        for m in resultado:
            p = passos[m]
            for slot in p.get("slots", {}):
                p["slots"][slot] = construir_lista((p["id"], slot))
            lista.append(p)
        return lista

    return {"schema_version": 2, "trigger": gatilho, "steps": construir_lista(None)}
