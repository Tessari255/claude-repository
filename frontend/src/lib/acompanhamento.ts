// Como a interface fica sabendo do andamento de uma execução. O contrato é `Acompanhar`: chama `aoAtualizar` com
// cada estado novo da execução e termina quando ela chega a um estado final, quando `ativo()` fica falso (a tela
// foi embora) ou lançando o erro de comunicação. Hoje o transporte é consulta periódica; trocar por eventos (SSE)
// é escrever outro `Acompanhar` e trocá-lo em hooks/useExecucao.ts, sem mexer em quem consome.

import type { Run } from '../types'
import { execucaoTerminou } from './execucao'

export type Acompanhar = (id: string, aoAtualizar: (r: Run) => void, ativo: () => boolean) => Promise<void>

export const INTERVALO_POLLING_MS = 300

const esperar = (ms: number) => new Promise<void>((r) => setTimeout(r, ms))

export function criarPolling(
  buscar: (id: string) => Promise<Run>,
  { intervaloMs = INTERVALO_POLLING_MS, dormir = esperar }: { intervaloMs?: number; dormir?: (ms: number) => Promise<void> } = {},
): Acompanhar {
  return async (id, aoAtualizar, ativo) => {
    while (ativo()) {
      const r = await buscar(id)
      aoAtualizar(r)
      if (execucaoTerminou(r)) return
      await dormir(intervaloMs)
    }
  }
}
