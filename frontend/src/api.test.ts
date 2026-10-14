import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'
import type { Flow } from './types'

describe('o fluxo que vai ao servidor', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('validar, testar, exportar e salvar mandam o fluxo sem as variáveis que saíram do texto da expressão', async () => {
    const corpos: unknown[] = []
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
      corpos.push(JSON.parse(init.body as string))
      return new Response('{}', { status: 200 })
    }))
    const sobra = { step: 'apagado', output: 'resultado', path: '' }
    const flow = {
      schema_version: 2, trigger: { id: 'gatilho', type: 'g', version: 1, inputs: {}, params: {} },
      steps: [{ id: 'c', type: 'compor', version: 1, params: {}, inputs: { entrada: { expr: '1 + 1', refs: { x: sobra } } } }],
    } as Flow
    await api.validar(flow)
    await api.executar('p', flow)
    await api.exportarFluxo('n', '', flow)
    await api.salvarProjeto('p', { name: 'n', flow, base_revision: 1 })
    expect(corpos.map((c) => (c as { flow: Flow }).flow.steps[0].inputs.entrada)).toEqual(Array(4).fill({ expr: '1 + 1', refs: {} }))
    expect(flow.steps[0].inputs.entrada).toEqual({ expr: '1 + 1', refs: { x: sobra } })  // o rascunho fica como está
  })
})
