// Operações puras sobre o fluxo em passos: árvore, campos com conteúdo dinâmico e visibilidade.
// Nada aqui depende do React; as regras de visibilidade espelham o verificador do servidor
// (que continua sendo a fonte da verdade) para o seletor de conteúdo dinâmico responder na hora.

import type {
  BlockType, Campo, ExecutarApos, Flow, ParamDef, Passo, PortDef, PortTypes, Ref, Run, Step, TipoDado,
} from '../types'

export const ID_GATILHO = 'gatilho'
export const MAX_PROFUNDIDADE = 8

export const chaveDoTipo = (id: string, versao: number) => `${id}@${versao}`

export function aleatorio(prefixo: string): string {
  const bytes = new Uint8Array(5)
  crypto.getRandomValues(bytes)
  return `${prefixo}_${Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')}`
}

export function valorPadraoDoTipo(tipo: string): unknown {
  switch (tipo) {
    case 'numero': return 0
    case 'booleano': return false
    case 'lista': return []
    case 'json': return {}
    default: return ''
  }
}

const clonar = <T,>(v: T): T => structuredClone(v)

// ------------------------------------------------------------------ campos
export type CampoDinamico = { parts: (string | Ref)[] }

export const campoLiteral = (value: unknown): Campo => ({ value })

export function ehDinamico(c: Campo | undefined | null): c is CampoDinamico {
  return !!c && 'parts' in c && Array.isArray(c.parts)
}

/** As partes de um campo de texto: um valor fixo vira um único trecho de texto. */
export function partesDe(c: Campo | undefined | null): (string | Ref)[] {
  if (!c) return []
  if (ehDinamico(c)) return c.parts
  const v = (c as { value: unknown }).value
  return typeof v === 'string' && v !== '' ? [v] : v === null || v === undefined || v === '' ? [] : [String(v)]
}

/** Junta trechos vizinhos; sem nenhum conteúdo dinâmico, volta a ser um valor fixo de texto. */
export function campoDePartes(partes: (string | Ref)[]): Campo {
  const norm: (string | Ref)[] = []
  for (const p of partes) {
    if (typeof p === 'string') {
      if (p === '') continue
      if (typeof norm[norm.length - 1] === 'string') norm[norm.length - 1] = (norm[norm.length - 1] as string) + p
      else norm.push(p)
    } else norm.push(p)
  }
  if (norm.every((p) => typeof p === 'string')) return { value: norm.join('') }
  return { parts: norm }
}

export function referenciaUnica(c: Campo | undefined | null): Ref | null {
  if (!ehDinamico(c) || c.parts.length !== 1) return null
  const p = c.parts[0]
  return typeof p === 'string' ? null : p
}

export function referenciasDe(c: Campo | undefined | null): Ref[] {
  return ehDinamico(c) ? c.parts.filter((p): p is Ref => typeof p !== 'string') : []
}

/** O campo está “sem preenchimento”? (espelha o servidor: texto fixo vazio e nenhum conteúdo dinâmico contam como vazio) */
export function campoVazio(c: Campo | undefined | null, tipo: string): boolean {
  if (!c) return true
  if (ehDinamico(c)) return !c.parts.some((p) => typeof p !== 'string' || p !== '')
  const v = (c as { value: unknown }).value
  if (v === null || v === undefined) return true
  return tipo === 'texto' && typeof v === 'string' && v === ''
}

// ------------------------------------------------------------------ definição efetiva
export function portasDeclaradas(valor: unknown): PortDef[] {
  if (!Array.isArray(valor)) return []
  return valor.filter((p): p is PortDef => !!p && typeof p === 'object' && typeof (p as PortDef).id === 'string')
    .map((p) => ({
      ...p,
      required: p.required ?? true,
      description: p.description ?? '',
      type: p.type ?? 'qualquer',
      label: typeof p.label === 'string' && p.label ? p.label : p.id,
    }))
}

function padraoDoParam(def: BlockType, id: string): unknown {
  return def.params.find((p) => p.id === id)?.default
}

/** A definição do bloco já com as entradas/saídas que o próprio passo declara (Python inline, gatilho). */
export function definicaoEfetiva(def: BlockType, params: Record<string, unknown>): BlockType {
  if (!def.inputs_from && !def.outputs_from) return def
  const out = { ...def }
  if (def.inputs_from) out.inputs = portasDeclaradas(def.inputs_from in params ? params[def.inputs_from] : padraoDoParam(def, def.inputs_from))
  if (def.outputs_from) out.outputs = portasDeclaradas(def.outputs_from in params ? params[def.outputs_from] : padraoDoParam(def, def.outputs_from))
  return out
}

export function valorDoParametro(params: Record<string, unknown>, p: ParamDef): unknown {
  return p.id in params ? params[p.id] : p.default
}

export function parametroVisivel(def: BlockType, params: Record<string, unknown>, p: ParamDef): boolean {
  if (!p.visible_when) return true
  const ref = def.params.find((x) => x.id === p.visible_when!.param)
  const atual = ref ? valorDoParametro(params, ref) : params[p.visible_when.param]
  return p.visible_when.values.includes(atual as string)
}

/** Tipo efetivo de uma entrada (ex.: o valor inicial de uma variável acompanha o tipo escolhido). */
export function tipoDaEntrada(def: BlockType, params: Record<string, unknown>, porta: PortDef): TipoDado {
  const tf = porta.type_from
  if (tf?.param) {
    const p = def.params.find((x) => x.id === tf.param)
    const v = p ? valorDoParametro(params, p) : undefined
    if (typeof v === 'string' && ['texto', 'numero', 'booleano', 'lista', 'json'].includes(v)) return v as TipoDado
  }
  return porta.type
}

// ------------------------------------------------------------------ passos
export function novoPasso(def: BlockType): Passo {
  const params: Record<string, unknown> = {}
  for (const p of def.params) if (p.default !== null && p.default !== undefined) params[p.id] = clonar(p.default)
  const inputs: Record<string, Campo> = {}
  for (const i of def.inputs) if (i.default !== null && i.default !== undefined) inputs[i.id] = { value: clonar(i.default) }
  const passo: Passo = { id: aleatorio('stp'), type: def.id, version: def.version, label: null, inputs, params }
  if (def.slots.length) passo.slots = Object.fromEntries(def.slots.map((s) => [s.id, []]))
  return passo
}

/** Modelo de um bloco Python reutilizável a partir de um passo Python já configurado (código, entradas e saídas atuais). */
export function blocoPythonDoPasso(def: BlockType | undefined, p: Passo): BlockType | undefined {
  if (!def) return undefined
  const ef = definicaoEfetiva(def, p.params)
  return {
    ...def, id: 'custom.novo', name: p.label || 'Meu bloco Python', description: '', category: 'Personalizados', kind: 'python',
    code: String(p.params.codigo ?? def.params.find((x) => x.id === 'codigo')?.default ?? ''), inputs: ef.inputs, outputs: ef.outputs, params: [],
  }
}

export function fluxoVazio(gatilho?: BlockType): Flow {
  const params: Record<string, unknown> = {}
  for (const p of gatilho?.params ?? []) if (p.default !== null && p.default !== undefined) params[p.id] = clonar(p.default)
  return { schema_version: 2, trigger: { id: ID_GATILHO, type: 'builtin.gatilho_manual', version: 1, inputs: {}, params: { campos: [], ...params } }, steps: [] }
}

export interface Posicao {
  passo: Passo
  pai: Passo | null
  espaco: string | null
  indice: number
  ancestrais: Passo[]
  irmaos: Passo[]
}

export function* percorrer(passos: Passo[], pai: Passo | null = null, espaco: string | null = null, ancestrais: Passo[] = []): Generator<Posicao> {
  for (let i = 0; i < passos.length; i++) {
    const p = passos[i]
    yield { passo: p, pai, espaco, indice: i, ancestrais, irmaos: passos }
    for (const [slot, filhos] of Object.entries(p.slots ?? {})) yield* percorrer(filhos, p, slot, [...ancestrais, p])
  }
}

export function todosOsPassos(flow: Flow, comGatilho = true): Passo[] {
  const lista = [...percorrer(flow.steps)].map((x) => x.passo)
  return comGatilho ? [flow.trigger, ...lista] : lista
}

export const contarPassos = (flow: Flow) => [...percorrer(flow.steps)].length

export function achar(flow: Flow, id: string): Posicao | null {
  for (const pos of percorrer(flow.steps)) if (pos.passo.id === id) return pos
  return null
}

export function acharQualquer(flow: Flow, id: string): Passo | null {
  return id === flow.trigger.id ? flow.trigger : achar(flow, id)?.passo ?? null
}

function mapearLista(passos: Passo[], fn: (p: Passo) => Passo): Passo[] {
  return passos.map((p) => {
    const novo = fn(p)
    if (!novo.slots) return novo
    return { ...novo, slots: Object.fromEntries(Object.entries(novo.slots).map(([k, v]) => [k, mapearLista(v, fn)])) }
  })
}

/** Aplica `fn` ao passo `id` (inclusive o gatilho). Não altera o fluxo original. */
export function atualizarPasso(flow: Flow, id: string, fn: (p: Passo) => Passo): Flow {
  if (id === flow.trigger.id) return { ...flow, trigger: fn(flow.trigger) }
  const alvo = (lista: Passo[]): Passo[] => lista.map((p) => {
    if (p.id === id) return fn(p)
    if (!p.slots) return p
    return { ...p, slots: Object.fromEntries(Object.entries(p.slots).map(([k, v]) => [k, alvo(v)])) }
  })
  return { ...flow, steps: alvo(flow.steps) }
}

/** Sobe para `versao` os passos (no corpo do fluxo, não o gatilho) com o mesmo id e tipo de `alvo`; o fluxo original não muda. */
export function fixarVersao(flow: Flow, alvo: Pick<Passo, 'id' | 'type'>, versao: number): Flow {
  return {
    ...flow,
    steps: JSON.parse(JSON.stringify(flow.steps), (_k, v) => (v && typeof v === 'object' && v.id === alvo.id && v.type === alvo.type ? { ...v, version: versao } : v)),
  }
}

export interface Destino { paiId: string | null; espaco: string | null; indice: number }

/** Onde o novo passo vai entrar, em palavras, para o seletor de blocos. */
export function rotuloDoDestino(flow: Flow, d: Destino, nomeDe: (id: string) => string, def: ObterDef): string {
  if (d.paiId) {
    const pai = acharQualquer(flow, d.paiId)
    const espaco = d.espaco ? ` (${(pai && def(pai)?.slots.find((s) => s.id === d.espaco)?.label) ?? d.espaco})` : ''
    return `Dentro de “${nomeDe(d.paiId)}”${espaco}`
  }
  return d.indice === 0 ? 'Logo depois do gatilho' : `Depois de “${nomeDe(flow.steps[d.indice - 1].id)}”`
}

/** Profundidade (1 = lista principal) da lista de destino. */
export function profundidadeDoDestino(flow: Flow, d: Destino): number {
  if (!d.paiId) return 1
  const pos = achar(flow, d.paiId)
  return pos ? pos.ancestrais.length + 2 : 1
}

export function inserir(flow: Flow, d: Destino, passo: Passo): Flow {
  if (!d.paiId) {
    const lista = [...flow.steps]
    lista.splice(Math.min(d.indice, lista.length), 0, passo)
    return { ...flow, steps: lista }
  }
  return atualizarPasso(flow, d.paiId, (pai) => {
    const slots = { ...(pai.slots ?? {}) }
    const lista = [...(slots[d.espaco!] ?? [])]
    lista.splice(Math.min(d.indice, lista.length), 0, passo)
    slots[d.espaco!] = lista
    return { ...pai, slots }
  })
}

function removerDaLista(passos: Passo[], id: string): Passo[] {
  return passos.filter((p) => p.id !== id).map((p) => (p.slots
    ? { ...p, slots: Object.fromEntries(Object.entries(p.slots).map(([k, v]) => [k, removerDaLista(v, id)])) }
    : p))
}

export const remover = (flow: Flow, id: string): Flow => ({ ...flow, steps: removerDaLista(flow.steps, id) })

/** Sobe (-1) ou desce (+1) um passo dentro da própria lista. */
export function mover(flow: Flow, id: string, delta: -1 | 1): Flow {
  const pos = achar(flow, id)
  if (!pos) return flow
  const destino = pos.indice + delta
  if (destino < 0 || destino >= pos.irmaos.length) return flow
  const troca = (lista: Passo[]): Passo[] => {
    const nova = [...lista]
    ;[nova[pos.indice], nova[destino]] = [nova[destino], nova[pos.indice]]
    return nova
  }
  if (!pos.pai) return { ...flow, steps: troca(flow.steps) }
  return atualizarPasso(flow, pos.pai.id, (pai) => ({ ...pai, slots: { ...pai.slots, [pos.espaco!]: troca(pai.slots![pos.espaco!]) } }))
}

function todasAsReferencias(passo: Passo): Ref[] {
  const refs: Ref[] = []
  for (const c of Object.values(passo.inputs ?? {})) refs.push(...referenciasDe(c))
  const regras = passo.params?.regras
  if (Array.isArray(regras)) for (const r of regras) refs.push(...referenciasDe(r?.esq), ...referenciasDe(r?.dir))
  return refs
}

function reescreverReferencias(passo: Passo, troca: (r: Ref) => Ref): Passo {
  const campo = (c: Campo | null | undefined): Campo | null | undefined =>
    ehDinamico(c) ? { parts: c.parts.map((p) => (typeof p === 'string' ? p : troca(p))) } : c
  const inputs = Object.fromEntries(Object.entries(passo.inputs ?? {}).map(([k, v]) => [k, campo(v) as Campo]))
  let params = passo.params
  if (Array.isArray(params?.regras)) {
    params = { ...params, regras: params.regras.map((r) => ({ ...r, esq: campo(r.esq), ...(r.dir ? { dir: campo(r.dir) } : {}) })) }
  }
  return { ...passo, inputs, params }
}

/** Copia um passo (e tudo o que há dentro dele) logo depois do original, com ids novos. */
export function duplicar(flow: Flow, id: string): { flow: Flow; novoId: string | null } {
  const pos = achar(flow, id)
  if (!pos) return { flow, novoId: null }
  const mapa = new Map<string, string>()
  const copiar = (p: Passo): Passo => {
    const novo = aleatorio('stp')
    mapa.set(p.id, novo)
    return {
      ...clonar(p), id: novo,
      slots: p.slots ? Object.fromEntries(Object.entries(p.slots).map(([k, v]) => [k, v.map(copiar)])) : undefined,
    }
  }
  let copia = copiar(pos.passo)
  // referências a passos copiados passam a apontar para a cópia; as demais continuam apontando para o original
  const religar = (p: Passo): Passo => {
    const r = reescreverReferencias(p, (ref) => (mapa.has(ref.step) ? { ...ref, step: mapa.get(ref.step)! } : ref))
    return r.slots ? { ...r, slots: Object.fromEntries(Object.entries(r.slots).map(([k, v]) => [k, v.map(religar)])) } : r
  }
  copia = religar(copia)
  copia.label = pos.passo.label ? `${pos.passo.label} (cópia)` : null
  return { flow: inserir(flow, { paiId: pos.pai?.id ?? null, espaco: pos.espaco, indice: pos.indice + 1 }, copia), novoId: copia.id }
}

/** Quando o usuário troca o id de uma saída declarada (campo do gatilho, saída do Python), as referências a ela acompanham. */
export function renomearSaida(flow: Flow, stepId: string, de: string, para: string): Flow {
  if (de === para) return flow
  const troca = (r: Ref): Ref => (r.step === stepId && r.output === de ? { ...r, output: para } : r)
  return {
    ...flow,
    trigger: reescreverReferencias(flow.trigger, troca),
    steps: mapearLista(flow.steps, (p) => reescreverReferencias(p, troca)),
  }
}

export function renomearEntrada(passo: Passo, de: string, para: string): Passo {
  if (de === para || !(de in (passo.inputs ?? {}))) return passo
  const inputs: Record<string, Campo> = {}
  for (const [k, v] of Object.entries(passo.inputs)) inputs[k === de ? para : k] = v
  return { ...passo, inputs }
}

export function usaOPasso(flow: Flow, id: string): boolean {
  return todosOsPassos(flow).some((p) => p.id !== id && todasAsReferencias(p).some((r) => r.step === id))
}

// ------------------------------------------------------------------ visibilidade do conteúdo dinâmico
type ObterDef = (p: Passo) => BlockType | undefined

function exportados(lista: Passo[], def: ObterDef): string[] {
  const ids: string[] = []
  for (const p of lista) {
    ids.push(p.id)
    const d = def(p)
    for (const s of d?.slots ?? []) if (s.transparent) ids.push(...exportados(p.slots?.[s.id] ?? [], def))
  }
  return ids
}

/** O que o passo `alvo` enxerga: os passos que já rodaram (`antes`) e os contêineres que o envolvem (`dentro`). */
export function visiveisPara(flow: Flow, alvo: string, def: ObterDef): { antes: string[]; dentro: string[] } | null {
  if (alvo === flow.trigger.id) return { antes: [], dentro: [] }
  const visitar = (lista: Passo[], visiveis: string[], dentro: string[]): { antes: string[]; dentro: string[] } | null => {
    const locais: string[] = []
    for (const p of lista) {
      if (p.id === alvo) return { antes: [...visiveis, ...locais], dentro }
      const d = def(p)
      if (p.slots && d?.slots.length) {
        const transparentes: string[] = []
        for (const s of d.slots) {
          const filhos = p.slots[s.id] ?? []
          const r = visitar(filhos, [...visiveis, ...locais], [...dentro, p.id])
          if (r) return r
          if (s.transparent) transparentes.push(...exportados(filhos, def))
        }
        locais.push(p.id, ...transparentes)
      } else locais.push(p.id)
    }
    return null
  }
  return visitar(flow.steps, [flow.trigger.id], [])
}

export interface SaidaDinamica {
  ref: Ref
  rotulo: string
  tipo: TipoDado
  descricao: string
}
export interface GrupoDinamico {
  passoId: string
  nome: string
  tipoId: string
  dentro: boolean
  saidas: SaidaDinamica[]
}

export function nomeDoPasso(p: Passo, def?: BlockType | null): string {
  return p.label || def?.name || p.type
}

/** Todos os conteúdos dinâmicos disponíveis para o passo `alvo`, agrupados pelo passo de origem. */
export function conteudoDinamicoPara(flow: Flow, alvo: string, def: ObterDef, tipos: PortTypes): GrupoDinamico[] {
  const vis = visiveisPara(flow, alvo, def)
  if (!vis) return []
  const grupos: GrupoDinamico[] = []
  const adicionar = (id: string, dentro: boolean) => {
    const p = acharQualquer(flow, id)
    const d = p ? def(p) : undefined
    if (!p || !d) return
    const ef = definicaoEfetiva(d, p.params)
    const saidas = ef.outputs.filter((o) => !!o.inside === dentro).map((o) => ({
      ref: { step: id, output: o.id, path: '' }, rotulo: o.label,
      tipo: (tipos[id]?.outputs?.[o.id] ?? o.type) as TipoDado, descricao: o.description,
    }))
    if (saidas.length) grupos.push({ passoId: id, nome: nomeDoPasso(p, d), tipoId: d.id, dentro, saidas })
  }
  vis.dentro.slice().reverse().forEach((id) => adicionar(id, true))
  vis.antes.slice().reverse().forEach((id) => adicionar(id, false))
  return grupos
}

export function tipoCompativel(origem: string, destino: string): boolean {
  return origem === destino || origem === 'qualquer' || destino === 'qualquer'
}

/** Texto curto de um campo para resumos (“Olá, {Nome}!”). */
export function descreverCampo(c: Campo | null | undefined, nomeDe: (ref: Ref) => string): string {
  if (!c) return ''
  if (!ehDinamico(c)) {
    const v = (c as { value: unknown }).value
    return typeof v === 'string' ? v : JSON.stringify(v)
  }
  return c.parts.map((p) => (typeof p === 'string' ? p : `{${nomeDe(p)}}`)).join('')
}

export const ROTULO_EXECUTAR_APOS: Record<ExecutarApos, string> = {
  sucesso: 'teve sucesso', falhou: 'falhou', ignorado: 'foi ignorado', expirou: 'expirou',
}

export function executarAposPadrao(p: Passo): boolean {
  const r = p.run_after ?? ['sucesso']
  return r.length === 1 && r[0] === 'sucesso'
}

// ------------------------------------------------------------------ execuções
function mesmaIteracao(a: number[], b: number[]) {
  return a.length === b.length && a.every((v, i) => v === b[i])
}

/** A linha do histórico de um passo, na repetição escolhida de cada laço que o envolve. */
export function linhaDoPasso(run: Run | null, stepId: string, contexto: number[]): Step | null {
  if (!run) return null
  for (let n = contexto.length; n >= 0; n--) {
    const alvo = contexto.slice(0, n)
    const linha = run.steps.find((s) => s.step_id === stepId && mesmaIteracao(s.iteration, alvo))
    if (linha) return linha
  }
  return null
}

/** Quantas repetições um laço teve de fato (a partir das linhas dos passos de dentro dele). */
export function repeticoesDoLaco(run: Run | null, laco: Passo, contexto: number[]): number {
  if (!run) return 0
  const ids = new Set<string>()
  const coletar = (p: Passo) => Object.values(p.slots ?? {}).forEach((l) => l.forEach((f) => { ids.add(f.id); coletar(f) }))
  coletar(laco)
  let max = -1
  for (const s of run.steps) {
    if (!ids.has(s.step_id) || s.iteration.length <= contexto.length) continue
    if (contexto.every((v, i) => s.iteration[i] === v)) max = Math.max(max, s.iteration[contexto.length])
  }
  return max + 1
}

export const CONTEINERES_DE_LACO = new Set(['builtin.para_cada', 'builtin.repetir_ate'])

export const chaveIteracao = (id: string, contexto: number[]) => `${id}@${contexto.join('.')}`

/** Qual repetição de um laço está sendo mostrada: a escolhida pelo usuário ou, por padrão, a que falhou (ou a primeira). */
export function iteracaoEscolhida(run: Run | null, laco: Passo, contexto: number[], escolhas: Record<string, number>): { total: number; atual: number } {
  const total = repeticoesDoLaco(run, laco, contexto)
  const linha = linhaDoPasso(run, laco.id, contexto)
  const padrao = linha?.state === 'falhou' && total > 0 ? total - 1 : 0
  return { total, atual: Math.min(escolhas[chaveIteracao(laco.id, contexto)] ?? padrao, Math.max(0, total - 1)) }
}

/** A repetição escolhida em cada laço que envolve o passo (do mais externo ao mais interno). */
export function contextoDoPasso(flow: Flow, id: string, run: Run | null, escolhas: Record<string, number>): number[] {
  const pos = achar(flow, id)
  if (!pos) return []
  const contexto: number[] = []
  for (const a of pos.ancestrais) {
    if (CONTEINERES_DE_LACO.has(a.type)) contexto.push(iteracaoEscolhida(run, a, contexto, escolhas).atual)
  }
  return contexto
}
