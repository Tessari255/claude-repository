import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiFailure } from '../api'
import { useNotificar } from '../components/ui'
import { montarDefs } from '../lib/catalogo'
import { corpoDoSalvamento, estaSujo, falhaDoSalvamento } from '../lib/rascunho'
import type { Flow, Issue, PortTypes, Project } from '../types'
import type { Catalogo } from './useCatalogo'
import { useHistorico } from './useHistorico'
import { useVivo } from './useVivo'

/** Pausa depois da última edição antes de pedir ao servidor que revalide o fluxo. */
export const ATRASO_REVALIDACAO_MS = 350

export interface Analise { issues: Issue[]; port_types: PortTypes }

/** O que está na tela agora: o fluxo em edição, o nome digitado e o projeto como o servidor o devolveu. */
export interface Rascunho { flow: Flow | null; nome: string; projeto: Project | null }

/**
 * - `salvo`: gravado, com a revisão nova.
 * - `conflito`: outra aba salvou depois que este fluxo foi aberto; quem chamou decide o que fazer.
 * - `invalido`: o servidor recusou com problemas no fluxo, que já estão na análise.
 * - `falhou`: qualquer outra falha, já avisada ao usuário.
 * - `ignorado`: nada a salvar agora (projeto não carregado, salvamento em andamento ou visão somente leitura).
 */
export type ResultadoDoSalvamento = 'salvo' | 'conflito' | 'invalido' | 'falhou' | 'ignorado'

/**
 * O projeto aberto no editor: carga (junto com o catálogo de blocos), rascunho com desfazer/refazer versus o que
 * está salvo, salvamento com revisão, verificação contínua pelo servidor e o aviso ao fechar a aba com alterações.
 * `somenteLeitura` (uma execução antiga em tela) suspende o salvamento e a verificação do rascunho.
 */
export function useProjeto(projectId: string, catalogo: Catalogo, somenteLeitura: boolean) {
  const notificar = useNotificar()
  const vivo = useVivo()
  const hist = useHistorico<Flow>()
  const flowAtual = hist.atual
  const { reiniciar } = hist
  const { iniciar: iniciarCatalogo, sistema } = catalogo
  const [falhaCarga, setFalhaCarga] = useState<ApiFailure | null>(null)
  const [projeto, setProjeto] = useState<Project | null>(null)
  const [nome, setNome] = useState('')
  const [salvoJson, setSalvoJson] = useState<string | null>(null)
  const [salvando, setSalvando] = useState(false)
  const [analise, setAnalise] = useState<Analise>({ issues: [], port_types: {} })
  const [verificando, setVerificando] = useState(false)

  const contadorValidacao = useRef(0)
  // O que está na tela agora, para tarefas assíncronas e ouvintes que não podem depender do fechamento de uma renderização antiga.
  const estado = useRef<Rascunho>({ flow: flowAtual, nome, projeto })
  estado.current = { flow: flowAtual, nome, projeto }
  const rascunhoAtual = useCallback(() => estado.current, [])

  // -------------------------------------------------------------------------- carga inicial
  useEffect(() => {
    let cancelado = false
    ;(async () => {
      try {
        const [p, blocos, sis] = await Promise.all([api.projeto(projectId), api.blocos(), api.sistema()])
        const defs = await montarDefs(blocos, p.flow, api.versaoDoBloco)
        if (cancelado) return
        setProjeto(p); setNome(p.name)
        iniciarCatalogo({ defs, biblioteca: blocos, sistema: sis })
        reiniciar(p.flow)
        setSalvoJson(JSON.stringify(p.flow))
      } catch (e) {
        if (!cancelado) setFalhaCarga(e as ApiFailure)
      }
    })()
    return () => { cancelado = true }
  }, [projectId, iniciarCatalogo, reiniciar])

  // -------------------------------------------------------------------------- rascunho versus salvo
  const fluxoJson = useMemo(() => (flowAtual ? JSON.stringify(flowAtual) : ''), [flowAtual])
  const sujo = estaSujo({ salvoJson, fluxoJson, nome, nomeSalvo: projeto?.name, somenteLeitura })

  useEffect(() => {
    const aviso = (e: BeforeUnloadEvent) => { if (sujo) { e.preventDefault(); e.returnValue = '' } }
    window.addEventListener('beforeunload', aviso)
    return () => window.removeEventListener('beforeunload', aviso)
  }, [sujo])

  // -------------------------------------------------------------------------- verificação contínua
  // O servidor é a única fonte de verdade das regras; só a resposta mais recente é aproveitada.
  const revalidar = useCallback(async () => {
    const f = estado.current.flow
    if (!f) return
    const minha = ++contadorValidacao.current
    setVerificando(true)
    try {
      const r = await api.validar(f)
      if (vivo.current && minha === contadorValidacao.current) setAnalise({ issues: r.issues, port_types: r.port_types })
    } catch { /* sem conexão: mantém a última análise */ } finally {
      if (vivo.current && minha === contadorValidacao.current) setVerificando(false)
    }
  }, [vivo])

  useEffect(() => {
    if (salvoJson === null || somenteLeitura) return
    const t = window.setTimeout(revalidar, ATRASO_REVALIDACAO_MS)
    return () => window.clearTimeout(t)
  }, [fluxoJson, salvoJson, revalidar, sistema?.executor.disponivel, somenteLeitura])

  // -------------------------------------------------------------------------- salvar
  const salvar = useCallback(async (): Promise<ResultadoDoSalvamento> => {
    const { flow: f, nome: n, projeto: p } = estado.current
    if (!p || !f || salvando || somenteLeitura) return 'ignorado'
    setSalvando(true)
    try {
      const salvo = await api.salvarProjeto(p.id, corpoDoSalvamento(p, n, f))
      setProjeto(salvo)
      setNome(salvo.name)
      setSalvoJson(JSON.stringify(f))
      notificar.sucesso('Fluxo salvo')
      void revalidar()
      return 'salvo'
    } catch (e) {
      const falha = falhaDoSalvamento(e as ApiFailure)
      if (falha.tipo === 'conflito') return 'conflito'
      notificar.erro('Não foi possível salvar', falha.detalhe)
      if (falha.issues.length) {
        setAnalise((a) => ({ ...a, issues: falha.issues }))
        return 'invalido'
      }
      return 'falhou'
    } finally {
      setSalvando(false)
    }
  }, [salvando, somenteLeitura, notificar, revalidar])

  return { falhaCarga, projeto, nome, setNome, hist, fluxoJson, sujo, salvando, salvar, analise, verificando, revalidar, rascunhoAtual }
}
