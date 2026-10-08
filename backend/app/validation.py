"""Verificador de fluxo: valida o fluxo em passos, sempre ANTES de executar (e, para a estrutura, antes de salvar).

Dois escopos de problema:

* ``estrutura``    — ids repetidos, espaços/gatilho inválidos. Impedem salvar.
* ``configuracao`` — campos obrigatórios, conteúdo dinâmico inválido, variáveis, executor ausente…
                     Um rascunho assim pode ser salvo, mas a execução é recusada.

O conteúdo dinâmico só pode apontar para passos que **já rodaram** quando o passo atual executa: o gatilho,
os irmãos anteriores, os irmãos anteriores dos contêineres que envolvem o passo, e as saídas "de dentro" do
contêiner (o item do “Para cada”). Passos dentro de uma condição ou de um laço não ficam visíveis depois dele;
os de um escopo ficam.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .blocks.builtin import (
    OPERADORES, OPERADORES_DE_ORDEM, OPERADORES_UNARIOS, VALIDADORES, _parse_numero, requer_sandbox,
)
from .config import Limites
from .models import BlockType, Campo, Flow, ID_GATILHO, Issue, ParamDef, Passo, Ref
from .passos import definicao_efetiva, percorrer, portas_declaradas, regras_declaradas
from .tipos import ROTULOS_TIPO, descrever_valor, rotulo_tipo, tipo_do_valor, tipos_compativeis, valor_e_do_tipo

Resolver = Callable[[str, int], BlockType | None]
UltimaVersao = Callable[[str], int | None]

CONTEINERES_DE_LACO = frozenset({"builtin.para_cada", "builtin.repetir_ate"})
TIPOS_QUE_ACEITAM_CAMINHO = frozenset({"json", "lista", "qualquer"})


@dataclass
class Analise:
    issues: list[Issue] = field(default_factory=list)
    defs: dict[str, BlockType] = field(default_factory=dict)       # passo -> bloco registrado (versão fixada)
    efetivas: dict[str, BlockType] = field(default_factory=dict)   # passo -> definição com portas declaradas
    port_types: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    ordem: list[str] = field(default_factory=list)                 # ids em ordem de documento (gatilho primeiro)

    @property
    def erros(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "erro"]

    @property
    def erros_de_estrutura(self) -> list[Issue]:
        return [i for i in self.erros if i.scope == "estrutura"]


def nome_passo(passo: Passo, tipo: BlockType | None) -> str:
    return passo.label or (tipo.name if tipo else passo.type)


# ---------------------------------------------------------------------------- parâmetros
def valor_efetivo(pdef: ParamDef, params: dict[str, Any]) -> Any:
    return params[pdef.id] if pdef.id in params else pdef.default


def parametro_visivel(pdef: ParamDef, tipo: BlockType, params: dict[str, Any]) -> bool:
    if pdef.visible_when is None:
        return True
    ref = tipo.param(pdef.visible_when.param)
    atual = valor_efetivo(ref, params) if ref else params.get(pdef.visible_when.param)
    return atual in pdef.visible_when.values


MAX_TEXTO_PARAM = 100_000         # caracteres em parâmetros de texto (o código tem limite próprio)
MAX_JSON_PARAM = 1024 * 1024      # bytes de uma lista/objeto JSON digitado em um parâmetro


def _grande_demais(pdef: ParamDef, valor: Any, rotulo: str) -> str | None:
    if isinstance(valor, str) and pdef.type != "codigo" and len(valor) > MAX_TEXTO_PARAM:
        return f"O texto de {rotulo} é grande demais (máximo de {MAX_TEXTO_PARAM:,} caracteres).".replace(",", ".")
    if isinstance(valor, (list, dict)) and len(json.dumps(valor, ensure_ascii=False, default=str)) > MAX_JSON_PARAM:
        return f"O conteúdo de {rotulo} é grande demais (máximo de {MAX_JSON_PARAM // 1024} KB)."
    return None


def mensagem_parametro(pdef: ParamDef, valor: Any) -> str | None:
    """Devolve a mensagem de erro do valor de um parâmetro, ou None se estiver válido."""
    rotulo = f"“{pdef.label}”"
    if valor is None:
        return f"O campo {rotulo} é obrigatório." if pdef.required else None
    grande = _grande_demais(pdef, valor, rotulo)
    if grande:
        return grande
    if pdef.type in ("texto", "codigo", "selecao", "variavel"):
        if not isinstance(valor, str):
            return f"O campo {rotulo} precisa ser um texto."
        if pdef.required and not pdef.allow_empty and valor.strip() == "" and pdef.type != "variavel":
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


def _faixa(pdef: ParamDef, valor: Any, rotulo: str) -> str | None:
    if pdef.min is not None and valor < pdef.min:
        return f"O valor de {rotulo} deve ser pelo menos {pdef.min:g}."
    if pdef.max is not None and valor > pdef.max:
        return f"O valor de {rotulo} deve ser no máximo {pdef.max:g}."
    return None


def parametros_efetivos(tipo: BlockType, params: dict[str, Any]) -> dict[str, Any]:
    """Parâmetros com padrões aplicados (cópias, para o código nunca mexer nos padrões); ignora o que não é visível."""
    completos = {p.id: valor_efetivo(p, params) for p in tipo.params}
    return {p.id: json.loads(json.dumps(completos[p.id])) for p in tipo.params
            if parametro_visivel(p, tipo, completos) and completos[p.id] is not None}


# ---------------------------------------------------------------------------- campos
def campo_vazio(campo: Campo | None, tipo: str) -> bool:
    """O campo está "sem preenchimento"? Texto fixo vazio e nenhum conteúdo dinâmico contam como vazio."""
    if campo is None:
        return True
    if campo.dinamico:
        return not any(isinstance(p, Ref) or p != "" for p in campo.parts or [])
    if campo.value is None:
        return True
    return tipo == "texto" and isinstance(campo.value, str) and campo.value == ""


# ---------------------------------------------------------------------------- análise
def analisar(flow: Flow, resolver: Resolver, *, sandbox: Any = None, ultima_versao: UltimaVersao | None = None,
             limites: Limites | None = None) -> Analise:
    """Analisa o fluxo.

    ``sandbox``: ``None`` = não verificar; objeto com ``disponivel`` e ``mensagem`` = verificar
    se há passos com código do usuário e o executor isolado está fora do ar.
    """
    a = Analise()

    def add(**kw: Any) -> None:
        a.issues.append(Issue(**kw))

    passos: dict[str, Passo] = {}
    todos: list[tuple[Passo, bool]] = [(flow.trigger, True)] + [(pos.passo, False) for pos in percorrer(flow.steps)]

    # --- estrutura: ids, tipos, gatilho e espaços
    if flow.trigger.id != ID_GATILHO:
        add(code="gatilho_invalido", scope="estrutura", step_id=flow.trigger.id,
            message=f"O identificador do gatilho precisa ser “{ID_GATILHO}”.")
    for p, eh_gatilho in todos:
        if p.id in passos:
            add(code="passo_duplicado", scope="estrutura", step_id=p.id,
                message=f"O identificador de passo “{p.id}” aparece mais de uma vez.")
            continue
        passos[p.id] = p
        t = resolver(p.type, p.version)
        if t is None:
            add(code="bloco_desconhecido", step_id=p.id,
                message=f"O bloco “{p.label or p.type}” (versão {p.version}) não está disponível nesta instalação.",
                hint="Se o fluxo veio de outra instalação, importe-o novamente para trazer os blocos personalizados.")
            continue
        if t.trigger != eh_gatilho:
            add(code="gatilho_invalido" if eh_gatilho else "passo_invalido", scope="estrutura", step_id=p.id,
                message=(f"“{t.name}” é um gatilho e não pode ser usado como passo." if t.trigger
                         else f"“{t.name}” não é um gatilho e não pode iniciar o fluxo."))
            continue
        a.defs[p.id] = t
        a.efetivas[p.id] = definicao_efetiva(t, p.params)
        extras = sorted(set(p.slots) - {s.id for s in t.slots})
        if extras:
            add(code="espaco_invalido", scope="estrutura", step_id=p.id,
                message=f"O passo “{nome_passo(p, t)}” tem passos dentro de “{', '.join(extras)}”, que não existe nesse bloco.")

    tipos: dict[tuple[str, str], str] = {}        # (passo, saída) -> tipo efetivo
    tipos_entrada: dict[str, dict[str, str]] = {}
    variaveis: dict[str, str] = {}                # passo "inicializar variável" -> tipo
    nomes_de_variavel: dict[str, str] = {}
    saidas_vistas = False

    def nome(step_id: str) -> str:
        return nome_passo(passos[step_id], a.defs.get(step_id)) if step_id in passos else step_id

    def tipo_do_campo(c: Campo) -> str:
        if c.dinamico:
            partes = c.parts or []
            if len(partes) == 1 and isinstance(partes[0], Ref):
                r = partes[0]
                return "qualquer" if r.path else tipos.get((r.step, r.output), "qualquer")
            return "texto"
        return tipo_do_valor(c.value) or "qualquer"

    def conferir_referencias(p: Passo, campo: Campo, field_id: str, rotulo: str, visiveis: set[str],
                             dentro: set[str]) -> None:
        for ref in campo.referencias():
            alvo = a.efetivas.get(ref.step)
            if alvo is None:
                if ref.step in passos:
                    continue  # bloco desconhecido: já reportado
                add(code="referencia_invalida", step_id=p.id, field=field_id,
                    message=f"{nome(p.id)}: “{rotulo}” usa o conteúdo de um passo que não existe mais.",
                    hint="Apague esse conteúdo dinâmico e escolha outro na lista de conteúdo dinâmico.")
                continue
            porta = alvo.output(ref.output)
            if ref.step in dentro:
                if porta is None or not porta.inside:
                    add(code="saida_inexistente", step_id=p.id, field=field_id,
                        message=f"{nome(p.id)}: “{rotulo}” usa “{nome(ref.step)}”, que ainda está rodando; só o item atual "
                                "e a posição ficam disponíveis dentro dele.")
                continue
            if ref.step not in visiveis:
                add(code="referencia_invalida", step_id=p.id, field=field_id,
                    message=f"{nome(p.id)}: “{rotulo}” usa o conteúdo de “{nome(ref.step)}”, que só roda depois deste passo "
                            "ou em outro caminho do fluxo.",
                    hint="Use conteúdo de passos que ficam antes deste (ou dentro do mesmo bloco).")
                continue
            if porta is None:
                add(code="saida_inexistente", step_id=p.id, field=field_id,
                    message=f"{nome(p.id)}: “{rotulo}” usa a saída “{ref.output}” de “{nome(ref.step)}”, que não existe.",
                    hint="Se o bloco foi atualizado, escolha o conteúdo dinâmico de novo.")
            elif porta.inside:
                add(code="saida_inexistente", step_id=p.id, field=field_id,
                    message=f"{nome(p.id)}: “{rotulo}” usa “{porta.label}”, que só existe dentro de “{nome(ref.step)}”.")
            elif ref.path and tipos.get((ref.step, ref.output), "qualquer") not in TIPOS_QUE_ACEITAM_CAMINHO:
                add(code="tipo_incompativel", step_id=p.id, field=field_id,
                    message=f"{nome(p.id)}: “{rotulo}” usa um campo interno de “{porta.label}”, mas esse conteúdo é "
                            f"{rotulo_tipo(tipos.get((ref.step, ref.output), 'qualquer'))}.")

    def conferir_tipo(p: Passo, campo: Campo, tipo_esperado: str, field_id: str, rotulo: str) -> None:
        obtido = tipo_do_campo(campo)
        if tipos_compativeis(obtido, tipo_esperado):
            return
        if campo.dinamico and obtido == "texto" and len(campo.parts or []) > 1:
            msg = (f"{nome(p.id)}: “{rotulo}” mistura texto com conteúdo dinâmico, o que gera um texto, "
                   f"mas o campo espera {rotulo_tipo(tipo_esperado)}.")
            dica = "Deixe só um conteúdo dinâmico no campo (sem texto ao redor)."
        else:
            msg = (f"{nome(p.id)}: “{rotulo}” recebe {rotulo_tipo(obtido)}, mas espera {rotulo_tipo(tipo_esperado)}.")
            dica = "Escolha um conteúdo do tipo certo ou converta o valor com outro passo (por exemplo, um bloco Python)."
        add(code="tipo_incompativel", step_id=p.id, field=field_id, message=msg, hint=dica)

    def tipo_entrada_efetivo(p: Passo, t: BlockType, porta: Any) -> str:
        if porta.type_from and porta.type_from.param:
            ref = t.param(porta.type_from.param)
            escolhido = valor_efetivo(ref, p.params) if ref else None
            if isinstance(escolhido, str) and escolhido in ROTULOS_TIPO:
                return escolhido
        return porta.type

    # ------------------------------------------------------------------ um passo
    def analisar_passo(p: Passo, visiveis: set[str], dentro: tuple[str, ...], dentro_de_laco: bool) -> None:
        nonlocal saidas_vistas
        t, ef = a.defs.get(p.id), a.efetivas.get(p.id)
        a.ordem.append(p.id)
        if t is None or ef is None or passos.get(p.id) is not p:
            return
        dentro_set = set(dentro)
        completos = {pd.id: valor_efetivo(pd, p.params) for pd in t.params}

        # --- entradas
        tipos_entrada[p.id] = {}
        conhecidas = {porta.id for porta in ef.inputs}
        for chave in p.inputs:
            if chave not in conhecidas:
                add(code="entrada_desconhecida", severity="aviso", step_id=p.id, field=chave,
                    message=f"O passo “{nome(p.id)}” tem o campo “{chave}”, que o bloco não usa. Ele será ignorado.")
        for porta in ef.inputs:
            esperado = tipo_entrada_efetivo(p, t, porta)
            tipos_entrada[p.id][porta.id] = esperado
            campo = p.inputs.get(porta.id)
            if campo is not None:
                conferir_referencias(p, campo, porta.id, porta.label, visiveis, dentro_set)
            if campo_vazio(campo, esperado):
                if porta.required and porta.default is None:
                    add(code="campo_obrigatorio", step_id=p.id, field=porta.id,
                        message=f"{nome(p.id)}: o campo “{porta.label}” é obrigatório.",
                        hint="Digite um valor ou escolha um conteúdo dinâmico de um passo anterior.")
                continue
            assert campo is not None
            conferir_tipo(p, campo, esperado, porta.id, porta.label)

        # --- parâmetros
        conhecidos = {pd.id for pd in t.params}
        for chave in p.params:
            if chave not in conhecidos:
                add(code="parametro_desconhecido", severity="aviso", step_id=p.id, field=chave,
                    message=f"O passo “{nome(p.id)}” tem a configuração “{chave}”, que a versão {p.version} não usa. Ela será ignorada.")
        for pd in t.params:
            if not parametro_visivel(pd, t, completos):
                continue
            valor = completos[pd.id]
            if pd.type == "regras":
                _regras(p, pd, valor, visiveis, dentro_set)
            elif pd.type == "portas":
                _portas(p, t, pd, valor)
            elif pd.type == "variavel":
                _variavel(p, t, pd, valor)
            else:
                msg = mensagem_parametro(pd, valor)
                if msg:
                    add(code="parametro_invalido", step_id=p.id, field=pd.id, message=f"{nome(p.id)}: {msg}")
        validador = VALIDADORES.get(t.id)
        if validador:
            for param_id, msg in validador(parametros_efetivos(t, p.params)):
                add(code="parametro_invalido", step_id=p.id, field=param_id, message=f"{nome(p.id)}: {msg}")

        # --- regras específicas de cada bloco
        if t.id == "builtin.var_inicializar":
            if dentro:
                add(code="variavel_fora_do_topo", step_id=p.id,
                    message=f"{nome(p.id)}: variáveis só podem ser criadas na lista principal do fluxo, fora de condições e laços.",
                    hint="Mova este passo para fora do bloco e use “Definir variável” lá dentro.")
            nome_var = str(completos.get("nome") or "").strip().lower()
            if nome_var:
                if nome_var in nomes_de_variavel and nomes_de_variavel[nome_var] != p.id:
                    add(code="variavel_duplicada", step_id=p.id, field="nome",
                        message=f"{nome(p.id)}: já existe uma variável chamada “{completos.get('nome')}”.")
                nomes_de_variavel.setdefault(nome_var, p.id)
            if not dentro:
                variaveis[p.id] = str(completos.get("tipo") or "texto")
        if t.id == "builtin.saida":
            saidas_vistas = True
            if dentro_de_laco:
                add(code="saida_em_laco", step_id=p.id,
                    message=f"{nome(p.id)}: a “Saída final” não pode ficar dentro de “Para cada” ou “Repetir até”.",
                    hint="Guarde os valores em uma variável de lista e mostre a lista depois do laço.")
        if p.settings.timeout_s is not None and limites is not None and p.settings.timeout_s > limites.tempo_s:
            add(code="tempo_limite_invalido", step_id=p.id, field="timeout_s",
                message=f"{nome(p.id)}: o tempo limite do passo não pode passar de {limites.tempo_s:g} s (limite do servidor).")
        if sandbox is not None and requer_sandbox(t, parametros_efetivos(t, p.params)) and not sandbox.disponivel:
            add(code="executor_indisponivel", step_id=p.id,
                message=(f"{nome(p.id)} executa código Python, mas o executor isolado não está disponível: "
                         f"{sandbox.mensagem or 'verifique a instalação do Docker'}"),
                hint=sandbox.instrucao or "A execução de código Python fica desabilitada até o executor estar disponível.")
        if ultima_versao is not None and t.kind == "python":
            ultima = ultima_versao(t.id)
            if ultima and ultima > p.version:
                add(code="versao_desatualizada", severity="aviso", step_id=p.id,
                    message=(f"Existe uma versão mais nova de “{t.name}” (v{ultima}). Este fluxo continua usando a v{p.version}, "
                             "sem mudanças."),
                    hint="Para usar a nova versão, atualize o bloco no painel de configuração e confira os campos.")

        # --- tipos das saídas e das portas
        saidas: dict[str, str] = {}
        for porta in ef.outputs:
            tipo_saida = porta.type
            if porta.type_from:
                if porta.type_from.param:
                    ref = t.param(porta.type_from.param)
                    escolhido = valor_efetivo(ref, p.params) if ref else None
                    if isinstance(escolhido, str) and escolhido in ROTULOS_TIPO:
                        tipo_saida = escolhido
                elif porta.type_from.input:
                    campo = p.inputs.get(porta.type_from.input)
                    tipo_saida = tipo_do_campo(campo) if campo is not None and not campo_vazio(campo, "qualquer") else "qualquer"
            tipos[(p.id, porta.id)] = tipo_saida
            saidas[porta.id] = tipo_saida
        a.port_types[p.id] = {"inputs": dict(tipos_entrada[p.id]), "outputs": saidas}

    # ------------------------------------------------------------------ parâmetros especiais
    def _regras(p: Passo, pd: ParamDef, valor: Any, visiveis: set[str], dentro: set[str]) -> None:
        regras, erro = regras_declaradas(valor)
        if erro:
            add(code="parametro_invalido", step_id=p.id, field=pd.id, message=f"{nome(p.id)}: {erro}")
            return
        operadores = {o.value for o in OPERADORES}
        for i, r in enumerate(regras, start=1):
            onde = f"condição {i}" if len(regras) > 1 else "condição"
            if r.op not in operadores:
                add(code="parametro_invalido", step_id=p.id, field=pd.id,
                    message=f"{nome(p.id)}: o teste da {onde} não existe: {r.op}.")
                continue
            conferir_referencias(p, r.esq, pd.id, f"Valor da {onde}", visiveis, dentro)
            if campo_vazio(r.esq, "texto"):
                add(code="campo_obrigatorio", step_id=p.id, field=pd.id,
                    message=f"{nome(p.id)}: escolha o valor a testar na {onde}.",
                    hint="Digite um valor ou escolha um conteúdo dinâmico de um passo anterior.")
                continue
            tipo_esq = tipo_do_campo(r.esq)
            if r.op in OPERADORES_UNARIOS:
                if r.op in ("verdadeiro", "falso") and tipo_esq not in ("booleano", "qualquer"):
                    add(code="tipo_incompativel", step_id=p.id, field=pd.id,
                        message=f"{nome(p.id)}: a {onde} espera um valor sim/não, mas recebe {rotulo_tipo(tipo_esq)}.",
                        hint="Use outro teste (ex.: “é igual a”) ou escolha um valor sim/não.")
                continue
            if r.dir is None:
                add(code="campo_obrigatorio", step_id=p.id, field=pd.id,
                    message=f"{nome(p.id)}: preencha o valor de comparação da {onde}.")
                continue
            conferir_referencias(p, r.dir, pd.id, f"Comparar com da {onde}", visiveis, dentro)
            if r.op in OPERADORES_DE_ORDEM and tipo_esq == "numero" and not r.dir.dinamico \
                    and isinstance(r.dir.value, str) and _parse_numero(r.dir.value) is None:
                add(code="parametro_invalido", step_id=p.id, field=pd.id,
                    message=f"{nome(p.id)}: digite um número válido para comparar na {onde} (ex.: 10 ou 2,5).")

    def _portas(p: Passo, t: BlockType, pd: ParamDef, valor: Any) -> None:
        portas, erro = portas_declaradas(valor)
        if erro:
            add(code="parametro_invalido", step_id=p.id, field=pd.id, message=f"{nome(p.id)}: {erro}")
        elif t.id == "builtin.python" and pd.id == "saidas" and not portas:
            add(code="parametro_invalido", step_id=p.id, field=pd.id,
                message=f"{nome(p.id)}: declare ao menos uma saída para o código Python.",
                hint="As saídas são os nomes das chaves do dicionário devolvido por run().")

    def _variavel(p: Passo, t: BlockType, pd: ParamDef, valor: Any) -> None:
        if not isinstance(valor, str) or valor == "":
            add(code="variavel_invalida", step_id=p.id, field=pd.id,
                message=f"{nome(p.id)}: escolha a variável.",
                hint="Crie uma variável antes com “Inicializar variável”.")
            return
        if valor not in variaveis:
            add(code="variavel_invalida", step_id=p.id, field=pd.id,
                message=f"{nome(p.id)}: a variável escolhida não existe (ou é criada só depois deste passo).",
                hint="Crie a variável com “Inicializar variável” antes deste passo.")
            return
        tipo_var = variaveis[valor]
        if t.id == "builtin.var_incrementar" and tipo_var != "numero":
            add(code="variavel_invalida", step_id=p.id, field=pd.id,
                message=f"{nome(p.id)}: só é possível incrementar variáveis do tipo número (“{nome(valor)}” é {rotulo_tipo(tipo_var)}).")
        if t.id == "builtin.var_acrescentar" and tipo_var != "lista":
            add(code="variavel_invalida", step_id=p.id, field=pd.id,
                message=f"{nome(p.id)}: só é possível acrescentar itens a variáveis do tipo lista (“{nome(valor)}” é {rotulo_tipo(tipo_var)}).")
        if t.id == "builtin.var_definir":
            campo = p.inputs.get("valor")
            if campo is not None and not campo_vazio(campo, "qualquer"):
                obtido = tipo_do_campo(campo)
                if not tipos_compativeis(obtido, tipo_var):
                    add(code="tipo_incompativel", step_id=p.id, field="valor",
                        message=f"{nome(p.id)}: a variável “{nome(valor)}” é {rotulo_tipo(tipo_var)}, mas o novo valor é {rotulo_tipo(obtido)}.")

    # ------------------------------------------------------------------ percurso
    def visitar_lista(lista: list[Passo], visiveis: list[str], dentro: tuple[str, ...], laco: bool) -> list[str]:
        """Analisa uma lista de passos. Devolve os ids que ficam visíveis depois dela (passos de escopos incluídos)."""
        locais: list[str] = []
        for p in lista:
            analisar_passo(p, set(visiveis) | set(locais), dentro, laco)
            t = a.defs.get(p.id)
            if t is not None and t.slots and passos.get(p.id) is p:
                transparentes: list[str] = []
                for s in t.slots:
                    exportados = visitar_lista(p.slots.get(s.id, []), visiveis + locais, (*dentro, p.id),
                                               laco or t.id in CONTEINERES_DE_LACO)
                    if s.transparent:
                        transparentes.extend(exportados)
                locais.append(p.id)
                locais.extend(transparentes)
            else:
                locais.append(p.id)
        return locais

    analisar_passo(flow.trigger, set(), (), False)
    if flow.trigger.id in a.efetivas:
        _gatilho_ok(a, flow.trigger, add)
    visitar_lista(flow.steps, [flow.trigger.id], (), False)

    # --- fluxo como um todo
    if not flow.steps:
        add(code="fluxo_vazio", message="O fluxo não tem nenhum passo.",
            hint="Clique em “+” abaixo do gatilho para adicionar o primeiro passo.")
    elif not saidas_vistas:
        add(code="sem_saida", severity="aviso",
            message="O fluxo não tem um passo “Saída final”, então nenhum resultado será exibido.",
            hint="Adicione uma “Saída final” e escolha o valor que quer ver.")
    return a


def _gatilho_ok(a: Analise, gatilho: Passo, add: Callable[..., None]) -> None:
    """Campos do gatilho: ids únicos e padrões compatíveis com o tipo."""
    ef = a.efetivas[gatilho.id]
    nomes_usados = [o.label.strip().lower() for o in ef.outputs]
    for dup in sorted({n for n in nomes_usados if nomes_usados.count(n) > 1}):
        add(code="parametro_invalido", step_id=gatilho.id, field="campos",
            message=f"O gatilho tem dois campos com o nome “{dup}”.")
    for porta in ef.outputs:
        if porta.default is not None and porta.type != "qualquer" and not valor_e_do_tipo(porta.default, porta.type):
            add(code="parametro_invalido", step_id=gatilho.id, field="campos",
                message=f"O valor padrão do campo “{porta.label}” deveria ser {rotulo_tipo(porta.type)}.")
