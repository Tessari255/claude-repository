// O que o editor entende e diz sobre uma execução em andamento, sem React e sem rede: quando ela terminou,
// qual passo está rodando e as mensagens que leitores de tela recebem. Os textos são parte da interface.

import type { Issue, Run } from '../types'

const ESTADOS_FINAIS: readonly Run['state'][] = ['concluido', 'falhou', 'cancelado']

export const execucaoTerminou = (r: Pick<Run, 'state'>) => ESTADOS_FINAIS.includes(r.state)

/** O primeiro passo que está rodando neste instante, se algum. */
export const passoEmExecucao = (r: Pick<Run, 'steps'>): string | null => r.steps.find((s) => s.state === 'executando')?.step_id ?? null

export function anuncioDeTermino(r: Pick<Run, 'state' | 'duration_ms' | 'error'>): string {
  if (r.state === 'concluido') return `Teste concluído em ${r.duration_ms} milissegundos.`
  if (r.state === 'cancelado') return 'A execução foi cancelada.'
  return `O teste falhou no passo ${r.error?.step_name ?? ''}: ${r.error?.message ?? ''}`
}

export interface FalhaDoTeste {
  /** O servidor recusou o fluxo por problemas nele: o usuário deve ir ao verificador. */
  fluxoInvalido: boolean
  titulo: string
  detalhe: string
}

export function falhaDoTeste(x: { code: string; message: string; issues: Issue[] }): FalhaDoTeste {
  if (x.issues.length && x.code === 'fluxo_invalido') {
    const n = x.issues.length
    return {
      fluxoInvalido: true,
      titulo: 'O fluxo não foi testado',
      detalhe: `${n} ${n === 1 ? 'problema precisa' : 'problemas precisam'} ser corrigido(s). Veja o verificador de fluxo.`,
    }
  }
  return { fluxoInvalido: false, titulo: 'Não foi possível testar', detalhe: [x.message, x.issues[0]?.message].filter(Boolean).join(' ') }
}
