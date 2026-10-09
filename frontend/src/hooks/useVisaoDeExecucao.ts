import { useCallback, useState } from 'react'
import type { Flow, Run } from '../types'

/** Uma execução antiga aberta no editor: ela vem com o fluxo como ele era naquele momento. */
export interface Visao { run: Run; flow: Flow }

/**
 * Modo "vendo uma execução do histórico": o editor mostra `atual.flow` em somente leitura no lugar do
 * rascunho, que fica intacto. Guarda também a repetição escolhida em cada laço, usada para ler a execução.
 */
export function useVisaoDeExecucao() {
  const [atual, setAtual] = useState<Visao | null>(null)
  const [iteracoes, setIteracoes] = useState<Record<string, number>>({})

  const abrir = useCallback((v: Visao) => { setAtual(v); setIteracoes({}) }, [])
  /** Sai da visão sem mexer nas repetições escolhidas (quem vai iniciar um teste já as zera). */
  const fechar = useCallback(() => setAtual(null), [])
  const voltar = useCallback(() => { setAtual(null); setIteracoes({}) }, [])
  const escolherIteracao = useCallback((chave: string, n: number) => setIteracoes((m) => ({ ...m, [chave]: n })), [])

  return { atual, iteracoes, abrir, fechar, voltar, escolherIteracao }
}
