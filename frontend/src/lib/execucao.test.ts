import { describe, expect, it } from 'vitest'
import type { Estado, Issue, Run, Step } from '../types'
import { criarPolling } from './acompanhamento'
import { anuncioDeTermino, execucaoTerminou, falhaDoTeste, passoEmExecucao } from './execucao'

const problema = (message: string): Issue => ({ code: 'x', severity: 'erro', scope: 'configuracao', message })
const passo = (step_id: string, state: Estado) => ({ step_id, state }) as Step
const execucao = (state: Estado, extra: Partial<Run> = {}) => ({ id: 'exe_1', state, steps: [], duration_ms: 12, error: null, ...extra }) as Run

describe('o que o editor diz sobre uma execução', () => {
  it('só concluída, falha e cancelada são estados finais', () => {
    for (const s of ['concluido', 'falhou', 'cancelado'] as const) expect(execucaoTerminou({ state: s })).toBe(true)
    for (const s of ['aguardando', 'executando', 'ignorado'] as const) expect(execucaoTerminou({ state: s })).toBe(false)
  })

  it('acha o passo que está rodando agora, e nenhum quando todos já terminaram', () => {
    expect(passoEmExecucao({ steps: [passo('a', 'concluido'), passo('b', 'executando'), passo('c', 'aguardando')] })).toBe('b')
    expect(passoEmExecucao({ steps: [passo('a', 'concluido'), passo('b', 'concluido')] })).toBeNull()
    expect(passoEmExecucao({ steps: [] })).toBeNull()
  })

  it('o anúncio de término distingue sucesso, cancelamento e falha', () => {
    expect(anuncioDeTermino(execucao('concluido', { duration_ms: 87 }))).toBe('Teste concluído em 87 milissegundos.')
    expect(anuncioDeTermino(execucao('cancelado'))).toBe('A execução foi cancelada.')
    const erro = { step_id: 's', step_name: 'Somar', code: 'x', message: 'Divisão por zero.', suggestion: null, technical: null }
    expect(anuncioDeTermino(execucao('falhou', { error: erro }))).toBe('O teste falhou no passo Somar: Divisão por zero.')
    expect(anuncioDeTermino(execucao('falhou'))).toBe('O teste falhou no passo : ')
  })

  it('fluxo inválido leva ao verificador e conta os problemas no singular e no plural', () => {
    const um = falhaDoTeste({ code: 'fluxo_invalido', message: 'm', issues: [problema('a')] })
    expect(um).toEqual({
      fluxoInvalido: true, titulo: 'O fluxo não foi testado',
      detalhe: '1 problema precisa ser corrigido(s). Veja o verificador de fluxo.',
    })
    const dois = falhaDoTeste({ code: 'fluxo_invalido', message: 'm', issues: [problema('a'), problema('b')] })
    expect(dois.detalhe).toBe('2 problemas precisam ser corrigido(s). Veja o verificador de fluxo.')
  })

  it('qualquer outra falha mostra a mensagem do servidor e o primeiro problema, se houver', () => {
    expect(falhaDoTeste({ code: 'sem_conexao', message: 'Sem conexão.', issues: [] }))
      .toEqual({ fluxoInvalido: false, titulo: 'Não foi possível testar', detalhe: 'Sem conexão.' })
    expect(falhaDoTeste({ code: 'outro', message: 'Recusado.', issues: [problema('Falta a entrada.')] }).detalhe).toBe('Recusado. Falta a entrada.')
    // fluxo_invalido sem problemas anexos não manda ninguém ao verificador (não haveria o que mostrar)
    expect(falhaDoTeste({ code: 'fluxo_invalido', message: 'Inválido.', issues: [] }).fluxoInvalido).toBe(false)
  })
})

describe('acompanhamento por consulta periódica', () => {
  function cenario(estados: Estado[]) {
    const consultas: string[] = []
    const pausas: number[] = []
    const recebidas: Estado[] = []
    let i = 0
    const buscar = async (id: string) => { consultas.push(id); return execucao(estados[Math.min(i++, estados.length - 1)]) }
    const dormir = async (ms: number) => { pausas.push(ms) }
    return { consultas, pausas, recebidas, buscar, dormir, aoAtualizar: (r: Run) => recebidas.push(r.state) }
  }

  it('entrega cada estado, espera entre as consultas e para no primeiro estado final', async () => {
    const c = cenario(['aguardando', 'executando', 'concluido', 'executando'])
    await criarPolling(c.buscar, { dormir: c.dormir })('exe_9', c.aoAtualizar, () => true)
    expect(c.recebidas).toEqual(['aguardando', 'executando', 'concluido'])
    expect(c.consultas).toEqual(['exe_9', 'exe_9', 'exe_9'])
    expect(c.pausas).toEqual([300, 300]) // nenhuma pausa depois do estado final
  })

  it('o intervalo é configurável', async () => {
    const c = cenario(['executando', 'falhou'])
    await criarPolling(c.buscar, { intervaloMs: 50, dormir: c.dormir })('exe_9', c.aoAtualizar, () => true)
    expect(c.pausas).toEqual([50])
  })

  it('para sem consultar de novo quando a tela já foi embora', async () => {
    const c = cenario(['executando'])
    let consultasPermitidas = 2
    await criarPolling(c.buscar, { dormir: c.dormir })('exe_9', c.aoAtualizar, () => consultasPermitidas-- > 0)
    expect(c.consultas).toHaveLength(2)
    expect(c.recebidas).toEqual(['executando', 'executando'])
  })

  it('não consulta nada se a tela já tinha ido embora antes de começar', async () => {
    const c = cenario(['executando'])
    await criarPolling(c.buscar, { dormir: c.dormir })('exe_9', c.aoAtualizar, () => false)
    expect(c.consultas).toEqual([])
  })

  it('a perda de contato com o servidor sobe para quem acompanha', async () => {
    const c = cenario(['executando'])
    let n = 0
    const buscar = async (id: string) => { if (n++ === 1) throw new Error('sem conexão'); return c.buscar(id) }
    await expect(criarPolling(buscar, { dormir: c.dormir })('exe_9', c.aoAtualizar, () => true)).rejects.toThrow('sem conexão')
    expect(c.recebidas).toEqual(['executando'])
  })
})
