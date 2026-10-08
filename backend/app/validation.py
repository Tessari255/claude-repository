"""Validação de fluxos, sempre ANTES de executar (e, para a estrutura, antes de salvar).

Dois escopos de problema:

* ``estrutura``    — ligações inválidas, tipos incompatíveis, ciclos, entradas duplicadas,
                     junção de caminhos condicionais. Impedem criar a conexão e salvar.
* ``configuracao`` — campos obrigatórios, entradas sem ligação, executor ausente. Um rascunho
                     assim pode ser salvo, mas a execução é recusada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .blocks.builtin import VALIDADORES, requer_sandbox
from .models import BlockInstance, BlockType, Connection, Flow, Issue, ParamDef
from .tipos import ROTULOS_TIPO, descrever_valor, rotulo_tipo, tipos_compativeis, valor_e_do_tipo

Resolver = Callable[[str, int], BlockType | None]
UltimaVersao = Callable[[str], int | None]


@dataclass
class Analise:
    issues: list[Issue] = field(default_factory=list)
    defs: dict[str, BlockType] = field(default_factory=dict)
    order: list[str] | None = None  # ordem topológica; None se houver ciclo
    port_types: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)

    @property
    def erros(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "erro"]

    @property
    def erros_de_estrutura(self) -> list[Issue]:
        return [i for i in self.erros if i.scope == "estrutura"]


def nome_bloco(bloco: BlockInstance, tipo: BlockType | None) -> str:
    return bloco.label or (tipo.name if tipo else bloco.type)


# ---------------------------------------------------------------------------- parâmetros
def valor_efetivo(pdef: ParamDef, params: dict[str, Any]) -> Any:
    return params[pdef.id] if pdef.id in params else pdef.default


def parametro_visivel(pdef: ParamDef, tipo: BlockType, params: dict[str, Any]) -> bool:
    if pdef.visible_when is None:
        return True
    ref = tipo.param(pdef.visible_when.param)
    atual = valor_efetivo(ref, params) if ref else params.get(pdef.visible_when.param)
    return atual in pdef.visible_when.values


def tipo_efetivo_param(pdef: ParamDef, tipo: BlockType, params: dict[str, Any]) -> str:
    """Tipo de dado do valor do parâmetro (a constante muda conforme o tipo escolhido)."""
    if pdef.type_from and pdef.type_from.param:
        ref = tipo.param(pdef.type_from.param)
        escolhido = valor_efetivo(ref, params) if ref else None
        if escolhido in ROTULOS_TIPO:
            return escolhido
    return {"texto": "texto", "codigo": "texto", "numero": "numero", "booleano": "booleano",
            "lista": "lista", "json": "json", "selecao": "texto"}[pdef.type]


def mensagem_parametro(pdef: ParamDef, valor: Any, tipo_dado: str) -> str | None:
    """Devolve a mensagem de erro do valor, ou None se estiver válido."""
    rotulo = f"“{pdef.label}”"
    if valor is None:
        return f"O campo {rotulo} é obrigatório." if pdef.required else None
    if pdef.type_from and pdef.type_from.param:
        # o tipo do valor depende de outro parâmetro (ex.: constante)
        if tipo_dado == "texto":
            if not isinstance(valor, str):
                return f"O campo {rotulo} precisa ser um texto."
            if pdef.required and not pdef.allow_empty and valor.strip() == "":
                return f"O campo {rotulo} é obrigatório."
            return None
        if not valor_e_do_tipo(valor, tipo_dado):
            return f"O campo {rotulo} precisa ser {_artigo(tipo_dado)} (recebeu {descrever_valor(valor)})."
        return _faixa(pdef, valor, rotulo) if tipo_dado == "numero" else None
    if pdef.type in ("texto", "codigo", "selecao"):
        if not isinstance(valor, str):
            return f"O campo {rotulo} precisa ser um texto."
        if pdef.required and not pdef.allow_empty and valor.strip() == "":
            return f"O campo {rotulo} é obrigatório."
        if pdef.type == "selecao" and valor not in {o.value for o in pdef.options}:
            return f"Escolha uma das opções disponíveis em {rotulo}."
        if pdef.type == "codigo" and len(valor) > 64 * 1024:
            return f"O código de {rotulo} é grande demais (máximo de 64 KB)."
        return None
    if pdef.type == "numero":
        if not valor_e_do_tipo(valor, "numero"):
            return f"O campo {rotulo} precisa ser um número."
        return _faixa(pdef, valor, rotulo)
    if pdef.type == "booleano" and not isinstance(valor, bool):
        return f"O campo {rotulo} precisa ser sim ou não."
    if pdef.type == "lista" and not isinstance(valor, list):
        return f"O campo {rotulo} precisa ser uma lista, como [1, 2, 3]."
    if pdef.type == "json" and not isinstance(valor, dict):
        return f"O campo {rotulo} precisa ser um objeto JSON, como {{\"chave\": \"valor\"}}."
    return None


def _artigo(tipo: str) -> str:
    return {"numero": "um número", "booleano": "sim ou não", "lista": "uma lista",
            "json": "um objeto JSON", "texto": "um texto"}.get(tipo, rotulo_tipo(tipo))


def _faixa(pdef: ParamDef, valor: Any, rotulo: str) -> str | None:
    if pdef.min is not None and valor < pdef.min:
        return f"O valor de {rotulo} deve ser pelo menos {pdef.min:g}."
    if pdef.max is not None and valor > pdef.max:
        return f"O valor de {rotulo} deve ser no máximo {pdef.max:g}."
    return None


def parametros_efetivos(tipo: BlockType, params: dict[str, Any]) -> dict[str, Any]:
    """Parâmetros com padrões aplicados; ignora o que não é visível nesta configuração."""
    completos = {p.id: valor_efetivo(p, params) for p in tipo.params}
    return {p.id: completos[p.id] for p in tipo.params
            if parametro_visivel(p, tipo, completos) and completos[p.id] is not None}


# ---------------------------------------------------------------------------- grafo
def _componentes_fortes(nos: list[str], arestas: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan. Devolve componentes com ciclo (tamanho > 1 ou laço em si mesmo)."""
    indice: dict[str, int] = {}
    baixo: dict[str, int] = {}
    pilha: list[str] = []
    na_pilha: set[str] = set()
    contador = [0]
    saida: list[list[str]] = []

    def visitar(v: str) -> None:
        indice[v] = baixo[v] = contador[0]
        contador[0] += 1
        pilha.append(v)
        na_pilha.add(v)
        for w in arestas.get(v, []):
            if w not in indice:
                visitar(w)
                baixo[v] = min(baixo[v], baixo[w])
            elif w in na_pilha:
                baixo[v] = min(baixo[v], indice[w])
        if baixo[v] == indice[v]:
            comp = []
            while True:
                w = pilha.pop()
                na_pilha.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1 or v in arestas.get(v, []):
                saida.append(comp)

    for n in nos:
        if n not in indice:
            visitar(n)
    return saida


def _caminho_do_ciclo(comp: list[str], arestas: dict[str, list[str]]) -> list[str]:
    """Um ciclo concreto dentro do componente, para mostrar ao usuário (A → B → A)."""
    dentro = set(comp)
    inicio = comp[0]
    fila = [[inicio]]
    visto = {inicio}
    while fila:
        caminho = fila.pop(0)
        for w in arestas.get(caminho[-1], []):
            if w == inicio:
                return caminho + [inicio]
            if w in dentro and w not in visto:
                visto.add(w)
                fila.append(caminho + [w])
    return comp + [comp[0]]


def ordem_topologica(ids: list[str], conexoes: list[Connection]) -> list[str] | None:
    """Kahn com desempate pela ordem da lista (nunca pela posição visual). None se houver ciclo."""
    grau = {i: 0 for i in ids}
    saida_de: dict[str, list[str]] = {i: [] for i in ids}
    for c in conexoes:
        if c.source.block in grau and c.target.block in grau:
            grau[c.target.block] += 1
            saida_de[c.source.block].append(c.target.block)
    posicao = {i: n for n, i in enumerate(ids)}
    prontos = sorted([i for i in ids if grau[i] == 0], key=posicao.get)
    resultado: list[str] = []
    while prontos:
        atual = prontos.pop(0)
        resultado.append(atual)
        for w in saida_de[atual]:
            grau[w] -= 1
            if grau[w] == 0:
                prontos.append(w)
        prontos.sort(key=posicao.get)
    return resultado if len(resultado) == len(ids) else None


# ---------------------------------------------------------------------------- análise
def analisar(flow: Flow, resolver: Resolver, *, sandbox: Any = None,
             ultima_versao: UltimaVersao | None = None) -> Analise:
    """Analisa o fluxo.

    ``sandbox``: ``None`` = não verificar; objeto com ``disponivel`` e ``mensagem`` = verificar
    se há blocos com código do usuário e o executor isolado está fora do ar.
    """
    a = Analise()
    issues = a.issues

    def add(**kw: Any) -> None:
        issues.append(Issue(**kw))

    # --- blocos
    vistos: set[str] = set()
    for b in flow.blocks:
        if b.id in vistos:
            add(code="bloco_duplicado", scope="estrutura", block_id=b.id,
                message=f"O identificador de bloco “{b.id}” aparece mais de uma vez.")
            continue
        vistos.add(b.id)
        t = resolver(b.type, b.version)
        if t is None:
            add(code="bloco_desconhecido", scope="estrutura", block_id=b.id,
                message=f"O bloco “{b.label or b.type}” (versão {b.version}) não está disponível nesta instalação.",
                hint="Se o fluxo veio de outra instalação, importe-o novamente para trazer os blocos personalizados.")
        else:
            a.defs[b.id] = t
    blocos = {b.id: b for b in flow.blocks}

    def nome(block_id: str) -> str:
        return nome_bloco(blocos[block_id], a.defs.get(block_id))

    # --- conexões (estrutura)
    validas: list[Connection] = []
    ids_conexao: set[str] = set()
    alvo_ocupado: dict[tuple[str, str], str] = {}
    for c in flow.connections:
        if c.id in ids_conexao:
            add(code="conexao_duplicada", scope="estrutura", connection_id=c.id,
                message=f"O identificador de conexão “{c.id}” aparece mais de uma vez.")
            continue
        ids_conexao.add(c.id)
        s, d = c.source, c.target
        if s.block not in blocos or d.block not in blocos:
            add(code="conexao_invalida", scope="estrutura", connection_id=c.id,
                message="Uma conexão aponta para um bloco que não existe mais.",
                hint="Remova a conexão e ligue os blocos novamente.")
            continue
        if s.block not in a.defs or d.block not in a.defs:
            continue  # já reportado como bloco desconhecido
        p_orig = a.defs[s.block].output(s.port)
        p_dest = a.defs[d.block].input(d.port)
        if p_orig is None or p_dest is None:
            falta = f"saída “{s.port}” de “{nome(s.block)}”" if p_orig is None else f"entrada “{d.port}” de “{nome(d.block)}”"
            add(code="conexao_invalida", scope="estrutura", connection_id=c.id, block_id=d.block,
                message=f"A conexão usa a {falta}, que não existe neste bloco.",
                hint="Se o bloco foi atualizado, remova a conexão e ligue novamente.")
            continue
        chave = (d.block, d.port)
        if chave in alvo_ocupado:
            add(code="entrada_duplicada", scope="estrutura", connection_id=c.id, block_id=d.block, port=d.port,
                message=f"A entrada “{p_dest.label}” de “{nome(d.block)}” já recebe dados de outro bloco.",
                hint="Cada entrada aceita uma única conexão. Remova a anterior ou use outra entrada.")
            continue
        alvo_ocupado[chave] = c.id
        validas.append(c)

    # --- ciclos
    ids = [b.id for b in flow.blocks if b.id in blocos]
    arestas: dict[str, list[str]] = {}
    for c in validas:
        arestas.setdefault(c.source.block, []).append(c.target.block)
    em_ciclo: set[str] = set()
    for comp in _componentes_fortes(ids, arestas):
        em_ciclo.update(comp)
        caminho = _caminho_do_ciclo(comp, arestas)
        conexoes_ciclo = [c.id for c in validas if c.source.block in comp and c.target.block in comp]
        if len(caminho) == 2:
            texto = f"“{nome(caminho[0])}” está ligado a si mesmo"
        else:
            texto = " → ".join(f"“{nome(n)}”" for n in caminho)
        add(code="ciclo", scope="estrutura", block_id=comp[0], connection_ids=conexoes_ciclo,
            message=f"Esta ligação cria um ciclo: {texto}. Nesta versão os fluxos não podem voltar a um bloco anterior.",
            hint="Remova a conexão que volta para um bloco anterior. Para repetir uma ação em uma lista, use o bloco “Para cada item da lista”.")
    a.order = None if em_ciclo else ordem_topologica(ids, validas)

    entrada_de = {(c.target.block, c.target.port): c for c in validas}

    # --- tipos efetivos das portas
    cache: dict[tuple[str, str], str] = {}

    def tipo_saida(block_id: str, port_id: str, visitando: frozenset = frozenset()) -> str:
        chave = (block_id, port_id)
        if chave in cache:
            return cache[chave]
        t = a.defs[block_id]
        porta = t.output(port_id)
        if porta is None:
            return "qualquer"
        resultado = porta.type
        if porta.type_from and chave not in visitando:
            if porta.type_from.param:
                ref = t.param(porta.type_from.param)
                escolhido = valor_efetivo(ref, blocos[block_id].params) if ref else None
                if escolhido in ROTULOS_TIPO:
                    resultado = escolhido
            elif porta.type_from.input:
                c = entrada_de.get((block_id, porta.type_from.input))
                if c is not None and c.source.block in a.defs:
                    resultado = tipo_saida(c.source.block, c.source.port, visitando | {chave})
        cache[chave] = resultado
        return resultado

    for bid, t in a.defs.items():
        a.port_types[bid] = {
            "inputs": {p.id: p.type for p in t.inputs},
            "outputs": {p.id: tipo_saida(bid, p.id) for p in t.outputs},
        }

    # --- compatibilidade de tipos
    for c in validas:
        origem = tipo_saida(c.source.block, c.source.port)
        p_dest = a.defs[c.target.block].input(c.target.port)
        if not tipos_compativeis(origem, p_dest.type):
            p_orig = a.defs[c.source.block].output(c.source.port)
            add(code="tipo_incompativel", scope="estrutura", connection_id=c.id,
                block_id=c.target.block, port=c.target.port,
                message=(f"Não é possível ligar a saída “{p_orig.label}” de “{nome(c.source.block)}” "
                         f"({rotulo_tipo(origem)}) à entrada “{p_dest.label}” de “{nome(c.target.block)}” "
                         f"({rotulo_tipo(p_dest.type)})."),
                hint="Os tipos precisam ser iguais. Converta o valor com outro bloco ou escolha outra porta.")

    # --- junção de caminhos condicionais (só com grafo sem ciclo)
    if a.order is not None:
        tags_bloco: dict[str, frozenset[tuple[str, str]]] = {}
        entradas_de: dict[str, list[Connection]] = {}
        for c in validas:
            entradas_de.setdefault(c.target.block, []).append(c)
        for bid in a.order:
            lista = []
            for c in entradas_de.get(bid, []):
                tags = set(tags_bloco.get(c.source.block, frozenset()))
                p = a.defs[c.source.block].output(c.source.port)
                if p is not None and p.conditional:
                    tags.add((c.source.block, c.source.port))
                lista.append((c, frozenset(tags)))
            lista.sort(key=lambda x: len(x[1]))
            cadeia = all(lista[i][1] <= lista[i + 1][1] for i in range(len(lista) - 1))
            tags_bloco[bid] = frozenset().union(*[t for _, t in lista]) if lista else frozenset()
            if not cadeia and bid in a.defs:
                caminhos = sorted({f"“{a.defs[o].output(p).label}” de “{nome(o)}”" for _, t in lista for o, p in t})
                add(code="juncao_condicional", scope="estrutura", block_id=bid,
                    connection_ids=[c.id for c, _ in lista],
                    message=(f"O bloco “{nome(bid)}” reúne caminhos condicionais diferentes ({'; '.join(caminhos)}). "
                             "Nesta versão não é possível juntar caminhos de uma condição em um mesmo bloco."),
                    hint="Use um bloco separado para cada caminho (por exemplo, duplique o bloco e ligue uma cópia em cada saída).")

    # --- configuração de cada bloco
    for b in flow.blocks:
        t = a.defs.get(b.id)
        if t is None or b.id not in blocos:
            continue
        completos = {p.id: valor_efetivo(p, b.params) for p in t.params}
        conhecidos = {p.id for p in t.params}
        for chave in b.params:
            if chave not in conhecidos:
                add(code="parametro_desconhecido", severity="aviso", block_id=b.id, param=chave,
                    message=f"O bloco “{nome(b.id)}” tem a configuração “{chave}”, que a versão {b.version} não usa. Ela será ignorada.")
        for p in t.params:
            if not parametro_visivel(p, t, completos):
                continue
            msg = mensagem_parametro(p, completos[p.id], tipo_efetivo_param(p, t, completos))
            if msg:
                add(code="parametro_invalido", block_id=b.id, param=p.id, message=f"{nome(b.id)}: {msg}")
        validador = VALIDADORES.get(t.id)
        if validador:
            for param_id, msg in validador(parametros_efetivos(t, b.params)):
                add(code="parametro_invalido", block_id=b.id, param=param_id, message=f"{nome(b.id)}: {msg}")
        for porta in t.inputs:
            if porta.required and (b.id, porta.id) not in entrada_de:
                add(code="entrada_obrigatoria", block_id=b.id, port=porta.id,
                    message=f"{nome(b.id)}: a entrada “{porta.label}” precisa estar ligada à saída de outro bloco.",
                    hint="Arraste da bolinha de saída de um bloco até esta entrada.")
        if sandbox is not None and requer_sandbox(t, parametros_efetivos(t, b.params)) and not sandbox.disponivel:
            add(code="executor_indisponivel", block_id=b.id,
                message=(f"{nome(b.id)} executa código Python, mas o executor isolado não está disponível: "
                         f"{sandbox.mensagem or 'verifique a instalação do Docker'}"),
                hint=sandbox.instrucao or "A execução de código personalizado fica desabilitada até o executor estar disponível.")
        if ultima_versao is not None and t.kind == "python":
            ultima = ultima_versao(t.id)
            if ultima and ultima > b.version:
                add(code="versao_desatualizada", severity="aviso", block_id=b.id,
                    message=(f"Existe uma versão mais nova de “{t.name}” (v{ultima}). Este fluxo continua usando a v{b.version}, "
                             "sem mudanças."),
                    hint="Para usar a nova versão, atualize o bloco no painel de configuração e confira as conexões.")

    # --- fluxo como um todo
    if not flow.blocks:
        add(code="fluxo_vazio", message="O fluxo está vazio.", hint="Arraste blocos da biblioteca para a área de trabalho.")
    elif not any(t.id == "builtin.saida" for t in a.defs.values()):
        add(code="sem_saida", severity="aviso",
            message="O fluxo não tem um bloco “Saída final”, então nenhum resultado será exibido.",
            hint="Adicione uma “Saída final” e ligue o valor que quer ver.")
    return a


def verificar_conexao(flow: Flow, nova: Connection, resolver: Resolver) -> list[Issue]:
    """Problemas de estrutura que a nova conexão introduziria (lista vazia = pode conectar)."""
    candidato = flow.model_copy(update={"connections": [*flow.connections, nova]})
    analise = analisar(candidato, resolver)
    return [i for i in analise.erros_de_estrutura if i.connection_id == nova.id or nova.id in i.connection_ids]
