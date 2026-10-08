import type { Edge, Node } from '@xyflow/react'
import type { BlockInstance, BlockType, Connection, Estado, Flow, ParamDef, Step, TipoDado } from '../types'

export interface NodeData extends Record<string, unknown> {
  block: Omit<BlockInstance, 'position'>
  def: BlockType | null
  step?: Step | null
  problemCount: number
  portTypes?: { inputs: Record<string, TipoDado>; outputs: Record<string, TipoDado> }
  latestVersion?: number | null
}

export type BlockNode = Node<NodeData, 'block'>
export type FlowEdge = Edge<{ skipped?: boolean }>

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

/** Parâmetros iniciais de um bloco novo: os valores padrão declarados pelo tipo. */
export function paramsIniciais(def: BlockType): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const p of def.params) {
    if (p.default !== null && p.default !== undefined) out[p.id] = structuredClone(p.default)
  }
  return out
}

export function valorDoParametro(def: BlockType, params: Record<string, unknown>, p: ParamDef): unknown {
  return p.id in params ? params[p.id] : p.default
}

export function parametroVisivel(def: BlockType, params: Record<string, unknown>, p: ParamDef): boolean {
  if (!p.visible_when) return true
  const ref = def.params.find((x) => x.id === p.visible_when!.param)
  const atual = ref ? valorDoParametro(def, params, ref) : params[p.visible_when.param]
  return p.visible_when.values.includes(atual as string)
}

/** Tipo de dado do valor de um parâmetro (a constante muda conforme o tipo escolhido). */
export function tipoEfetivoDoParametro(def: BlockType, params: Record<string, unknown>, p: ParamDef): string {
  if (p.type_from?.param) {
    const ref = def.params.find((x) => x.id === p.type_from!.param)
    const escolhido = ref ? valorDoParametro(def, params, ref) : null
    if (typeof escolhido === 'string') return escolhido
  }
  return p.type === 'codigo' || p.type === 'selecao' ? 'texto' : p.type
}

export function paraFluxo(nodes: BlockNode[], edges: FlowEdge[], viewport?: Flow['viewport']): Flow {
  return {
    schema_version: 1,
    blocks: nodes.map((n) => ({
      id: n.id,
      type: n.data.block.type,
      version: n.data.block.version,
      position: { x: n.position.x, y: n.position.y },
      params: n.data.block.params,
      label: n.data.block.label,
    })),
    connections: edges.map((e) => ({
      id: e.id,
      source: { block: e.source, port: e.sourceHandle ?? '' },
      target: { block: e.target, port: e.targetHandle ?? '' },
    })),
    viewport: viewport ?? null,
  }
}

export function deFluxo(flow: Flow, defs: Map<string, BlockType>): { nodes: BlockNode[]; edges: FlowEdge[] } {
  const nodes: BlockNode[] = flow.blocks.map((b) => ({
    id: b.id,
    type: 'block',
    position: { x: b.position.x, y: b.position.y },
    data: {
      block: { id: b.id, type: b.type, version: b.version, params: b.params, label: b.label },
      def: defs.get(chaveDoTipo(b.type, b.version)) ?? null,
      problemCount: 0,
    },
  }))
  const edges: FlowEdge[] = flow.connections.map((c) => ({
    id: c.id,
    source: c.source.block,
    sourceHandle: c.source.port,
    target: c.target.block,
    targetHandle: c.target.port,
    data: {},
  }))
  return { nodes, edges }
}

/** Copia os blocos selecionados (e as conexões entre eles) com ids novos, deslocados. */
export function duplicar(nodes: BlockNode[], edges: FlowEdge[], ids: Set<string>, deslocamento = 48) {
  const mapa = new Map<string, string>()
  const usados = new Set(nodes.map((n) => n.id))
  const novoId = () => {
    let id = aleatorio('blk')
    while (usados.has(id)) id = aleatorio('blk')
    usados.add(id)
    return id
  }
  const novosNos: BlockNode[] = nodes
    .filter((n) => ids.has(n.id))
    .map((n) => {
      const id = novoId()
      mapa.set(n.id, id)
      return {
        ...n,
        id,
        selected: true,
        position: { x: n.position.x + deslocamento, y: n.position.y + deslocamento },
        data: { ...n.data, block: { ...n.data.block, id, params: structuredClone(n.data.block.params) }, step: null, problemCount: 0 },
      }
    })
  const novasArestas: FlowEdge[] = edges
    .filter((e) => mapa.has(e.source) && mapa.has(e.target))
    .map((e) => ({ ...e, id: aleatorio('con'), source: mapa.get(e.source)!, target: mapa.get(e.target)!, selected: false }))
  return { nodes: novosNos, edges: novasArestas }
}

export function conexaoDe(params: { source: string; sourceHandle: string | null; target: string; targetHandle: string | null }): Connection {
  return {
    id: aleatorio('con'),
    source: { block: params.source, port: params.sourceHandle ?? '' },
    target: { block: params.target, port: params.targetHandle ?? '' },
  }
}

export const ROTULO_ESTADO: Record<Estado, string> = {
  aguardando: 'Aguardando',
  executando: 'Executando',
  concluido: 'Concluído',
  falhou: 'Falhou',
  ignorado: 'Ignorado',
}
