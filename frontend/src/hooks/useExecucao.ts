import { useCallback, useEffect, useState } from 'react'
import { api, ApiFailure } from '../api'
import { useNotificar } from '../components/ui'
import { criarPolling, type Acompanhar } from '../lib/acompanhamento'
import { anuncioDeTermino, execucaoTerminou, falhaDoTeste, passoEmExecucao } from '../lib/execucao'
import { acharQualquer, nomeDoPasso } from '../lib/modelo'
import type { Flow, Run } from '../types'
import type { Rascunho } from './useProjeto'
import type { VisaoDeExecucao } from './useVisaoDeExecucao'
import { useVivo } from './useVivo'

/**
 * Único ponto que conhece o transporte do acompanhamento de uma execução: hoje, consultas à API a cada 300 ms.
 * O item 12 do plano (SSE, com o polling como reserva) troca só esta linha; o resto do hook e da tela não mudam.
 */
const acompanharExecucao: Acompanhar = criarPolling((id) => api.execucao(id))

/**
 * - `iniciado`: o servidor aceitou e o hook já está acompanhando.
 * - `invalido`: o servidor recusou o fluxo por problemas nele (já revalidado e avisado): quem chamou abre o verificador.
 * - `falhou`: qualquer outra recusa, já avisada ao usuário.
 * - `ignorado`: nada a testar agora (projeto não carregado, teste em andamento ou fluxo vazio).
 */
export type ResultadoDoTeste = 'iniciado' | 'invalido' | 'falhou' | 'ignorado'

interface Opcoes {
  projectId: string
  /** O histórico só é pedido depois que o projeto abriu. */
  projetoCarregado: boolean
  rascunhoAtual: () => Rascunho
  revalidar: () => Promise<void>
  garantirDefs: (f: Flow) => Promise<void>
  visao: VisaoDeExecucao
}

/** Testar o fluxo no contêiner, acompanhar e cancelar a execução, e abrir uma execução do histórico em somente leitura. */
export function useExecucao({ projectId, projetoCarregado, rascunhoAtual, revalidar, garantirDefs, visao }: Opcoes) {
  const notificar = useNotificar()
  const vivo = useVivo()
  const { abrir: abrirVisao, voltar: voltarAoFluxo } = visao
  const [run, setRun] = useState<Run | null>(null)
  const [executando, setExecutando] = useState(false)
  const [historicoRuns, setHistoricoRuns] = useState<Run[]>([])

  useEffect(() => {
    if (!projetoCarregado) return
    let cancelado = false
    api.historico(projectId).then((h) => !cancelado && setHistoricoRuns(h)).catch(() => undefined)
    return () => { cancelado = true }
  }, [projectId, projetoCarregado])

  const acompanhar = useCallback(async (id: string) => {
    let anterior = ''
    try {
      await acompanharExecucao(id, (r) => {
        setRun(r)
        const atual = passoEmExecucao(r)
        if (atual && atual !== anterior) {
          anterior = atual
          const f = rascunhoAtual().flow
          const p = f ? acharQualquer(f, atual) : null
          notificar.anunciar(`Executando ${p ? nomeDoPasso(p, undefined) : 'passo'}.`)
        }
        if (execucaoTerminou(r)) notificar.anunciar(anuncioDeTermino(r))
      }, () => vivo.current)
    } catch (e) {
      notificar.erro('Perdemos o contato com o servidor durante o teste', (e as ApiFailure).message)
    } finally {
      if (vivo.current) {
        setExecutando(false)
        api.historico(projectId).then((h) => vivo.current && setHistoricoRuns(h)).catch(() => undefined)
      }
    }
  }, [notificar, projectId, vivo, rascunhoAtual])

  const testar = useCallback(async (dados?: Record<string, unknown>): Promise<ResultadoDoTeste> => {
    const { flow: f, projeto: p } = rascunhoAtual()
    if (!p || !f || executando) return 'ignorado'
    if (f.steps.length === 0) {
      notificar.info('O fluxo está vazio', 'Adicione ao menos um passo antes de testar.')
      return 'ignorado'
    }
    setExecutando(true); setRun(null); voltarAoFluxo()
    try {
      const r = await api.executar(p.id, f, dados)
      setRun(r)
      notificar.anunciar('Teste iniciado.')
      void acompanhar(r.id)
      return 'iniciado'
    } catch (e) {
      const falha = falhaDoTeste(e as ApiFailure)
      setExecutando(false)
      if (falha.fluxoInvalido) await revalidar()
      notificar.erro(falha.titulo, falha.detalhe)
      return falha.fluxoInvalido ? 'invalido' : 'falhou'
    }
  }, [executando, acompanhar, revalidar, notificar, voltarAoFluxo, rascunhoAtual])

  const cancelar = useCallback(async () => {
    if (!run) return
    try { await api.cancelarExecucao(run.id) } catch (e) { notificar.erro('Não foi possível cancelar', (e as ApiFailure).message) }
  }, [run, notificar])

  /** Abre uma execução do histórico em somente leitura, com o fluxo como ele era; devolve se conseguiu. */
  const abrirExecucao = useCallback(async (r: Run): Promise<boolean> => {
    try {
      const completa = await api.execucaoComFluxo(r.id)
      await garantirDefs(completa.flow)
      abrirVisao({ run: completa, flow: completa.flow })
      return true
    } catch (e) {
      notificar.erro('Não foi possível abrir a execução', (e as ApiFailure).message)
      return false
    }
  }, [garantirDefs, abrirVisao, notificar])

  return { run, executando, historicoRuns, testar, cancelar, abrirExecucao }
}
