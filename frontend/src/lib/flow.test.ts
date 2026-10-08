import { describe, expect, it } from 'vitest'
import type { BlockType, Flow } from '../types'
import { chaveDoTipo, deFluxo, duplicar, paraFluxo, parametroVisivel, paramsIniciais, tipoEfetivoDoParametro } from './flow'

const def = (over: Partial<BlockType> = {}): BlockType => ({
  id: 'builtin.x', version: 1, name: 'X', description: '', category: 'Dados', kind: 'builtin', icon: null,
  inputs: [], outputs: [], params: [], code: null, created_at: null, ...over,
})

const flow: Flow = {
  schema_version: 1,
  blocks: [
    { id: 'a', type: 'builtin.x', version: 1, position: { x: -10.5, y: 3.25 }, params: { v: { n: [1, 2] } }, label: 'Primeiro' },
    { id: 'b', type: 'builtin.x', version: 1, position: { x: 200, y: 0 }, params: {}, label: null },
    { id: 'c', type: 'builtin.x', version: 1, position: { x: 400, y: 0 }, params: {}, label: null },
  ],
  connections: [
    { id: 'k1', source: { block: 'a', port: 'o' }, target: { block: 'b', port: 'i' } },
    { id: 'k2', source: { block: 'b', port: 'o' }, target: { block: 'c', port: 'i' } },
  ],
  viewport: { x: 1, y: 2, zoom: 0.5 },
}
const defs = new Map([[chaveDoTipo('builtin.x', 1), def()]])

describe('conversão entre a API e o React Flow', () => {
  it('ida e volta preserva blocos, posições, parâmetros e conexões', () => {
    const { nodes, edges } = deFluxo(flow, defs)
    expect(paraFluxo(nodes, edges, flow.viewport)).toEqual(flow)
  })

  it('mantém o bloco mesmo quando a versão fixada não é conhecida', () => {
    const { nodes } = deFluxo(flow, new Map())
    expect(nodes[0].data.def).toBeNull()
    expect(nodes[0].data.block.version).toBe(1)
  })
})

describe('duplicação', () => {
  it('copia só as conexões entre os blocos selecionados, com ids novos', () => {
    const { nodes, edges } = deFluxo(flow, defs)
    const r = duplicar(nodes, edges, new Set(['a', 'b']))
    expect(r.nodes).toHaveLength(2)
    expect(r.edges).toHaveLength(1) // k1 (a→b); k2 sai da seleção e não é copiada
    const ids = new Set(nodes.map((n) => n.id))
    expect(r.nodes.every((n) => !ids.has(n.id))).toBe(true)
    expect(r.edges[0].source).toBe(r.nodes[0].id)
    expect(r.edges[0].target).toBe(r.nodes[1].id)
    expect(r.nodes[0].position).toEqual({ x: -10.5 + 48, y: 3.25 + 48 })
  })

  it('não compartilha referências de parâmetros com o original', () => {
    const { nodes, edges } = deFluxo(flow, defs)
    const r = duplicar(nodes, edges, new Set(['a']))
    ;(r.nodes[0].data.block.params as any).v.n.push(3)
    expect(nodes[0].data.block.params).toEqual({ v: { n: [1, 2] } })
  })
})

describe('parâmetros', () => {
  const constante = def({
    params: [
      { id: 'tipo', label: 'Tipo', type: 'selecao', required: true, default: 'texto', options: [], help: '', placeholder: '', multiline: false, allow_empty: false, min: null, max: null },
      { id: 'valor', label: 'Valor', type: 'texto', required: true, default: '', options: [], help: '', placeholder: '', multiline: false, allow_empty: true, min: null, max: null, type_from: { param: 'tipo' } },
      { id: 'sep', label: 'Sep', type: 'texto', required: false, default: ' ', options: [], help: '', placeholder: '', multiline: false, allow_empty: true, min: null, max: null, visible_when: { param: 'tipo', values: ['lista'] } },
    ],
  })

  it('aplica os padrões declarados', () => {
    expect(paramsIniciais(constante)).toEqual({ tipo: 'texto', valor: '', sep: ' ' })
  })

  it('o tipo do valor acompanha o parâmetro de que depende', () => {
    const valor = constante.params[1]
    expect(tipoEfetivoDoParametro(constante, {}, valor)).toBe('texto')
    expect(tipoEfetivoDoParametro(constante, { tipo: 'numero' }, valor)).toBe('numero')
  })

  it('esconde parâmetros que não se aplicam à operação escolhida', () => {
    const sep = constante.params[2]
    expect(parametroVisivel(constante, { tipo: 'texto' }, sep)).toBe(false)
    expect(parametroVisivel(constante, { tipo: 'lista' }, sep)).toBe(true)
  })
})
