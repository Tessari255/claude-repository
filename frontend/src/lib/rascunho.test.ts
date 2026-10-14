import { describe, expect, it } from 'vitest'
import type { Flow, Issue } from '../types'
import { corpoDoSalvamento, estaSujo, falhaDoSalvamento } from './rascunho'

const base = { salvoJson: '{"a":1}', fluxoJson: '{"a":1}', nome: 'Meu fluxo', nomeSalvo: 'Meu fluxo', somenteLeitura: false }
const flow = { schema_version: 2, trigger: { id: 'gatilho', type: 'g', version: 1, inputs: {}, params: {} }, steps: [] } as Flow
const problema = (message: string): Issue => ({ code: 'x', severity: 'erro', scope: 'configuracao', message })

describe('rascunho versus salvo', () => {
  it('igual ao que foi salvo não está sujo', () => {
    expect(estaSujo(base)).toBe(false)
  })

  it('mudar o fluxo ou o nome deixa o rascunho sujo', () => {
    expect(estaSujo({ ...base, fluxoJson: '{"a":2}' })).toBe(true)
    expect(estaSujo({ ...base, nome: 'Outro nome' })).toBe(true)
  })

  it('antes de carregar (nada salvo ainda) e na visão de uma execução antiga nunca está sujo', () => {
    expect(estaSujo({ ...base, salvoJson: null, fluxoJson: '', nome: '', nomeSalvo: undefined })).toBe(false)
    expect(estaSujo({ ...base, fluxoJson: '{"a":2}', nome: 'Outro', somenteLeitura: true })).toBe(false)
  })

  it('o corpo do salvamento leva a revisão de base e apara o nome', () => {
    const corpo = corpoDoSalvamento({ name: 'Salvo', revision: 7 }, '  Novo nome  ', flow)
    expect(corpo).toEqual({ name: 'Novo nome', flow, base_revision: 7 })
  })

  it('nome em branco mantém o nome já salvo', () => {
    expect(corpoDoSalvamento({ name: 'Salvo', revision: 1 }, '   ', flow).name).toBe('Salvo')
  })

  it('conflito de revisão é reconhecido pelo código, com ou sem problemas anexos', () => {
    expect(falhaDoSalvamento({ code: 'conflito_de_revisao', message: 'x', issues: [] })).toEqual({ tipo: 'conflito' })
    expect(falhaDoSalvamento({ code: 'conflito_de_revisao', message: 'x', issues: [problema('p')] })).toEqual({ tipo: 'conflito' })
  })

  it('outra falha junta a mensagem ao primeiro problema e devolve todos', () => {
    const issues = [problema('O campo A está vazio.'), problema('O campo B está vazio.')]
    expect(falhaDoSalvamento({ code: 'fluxo_invalido', message: 'Fluxo inválido.', issues }))
      .toEqual({ tipo: 'recusado', detalhe: 'Fluxo inválido. O campo A está vazio.', issues })
  })

  it('sem problemas anexos o detalhe é só a mensagem, sem espaço sobrando', () => {
    expect(falhaDoSalvamento({ code: 'sem_conexao', message: 'Sem conexão.', issues: [] }))
      .toEqual({ tipo: 'recusado', detalhe: 'Sem conexão.', issues: [] })
  })
})
