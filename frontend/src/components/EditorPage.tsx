import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiFailure } from '../api'
import {
  achar, acharQualquer, chaveDoTipo, contextoDoPasso, definicaoEfetiva, duplicar, inserir, linhaDoPasso, mover, nomeDoPasso, novoPasso, profundidadeDoDestino,
  remover, todosOsPassos, usaOPasso, MAX_PROFUNDIDADE, type Destino,
} from '../lib/modelo'
import { useAtalhos } from '../hooks/useAtalhos'
import { useHistorico } from '../hooks/useHistorico'
import { slugDeId } from '../lib/visual'
import type { BlockType, Flow, Issue, Passo, PortTypes, Project, Run, SystemInfo } from '../types'
import { BlockEditorDialog } from './BlockEditorDialog'
import { Designer, type AcaoDoPasso } from './Designer'
import { AtalhosDialog } from './Dialogs'
import { ErrorBoundary } from './ErrorBoundary'
import { Historico } from './Historico'
import { Icon, Logo } from './Icons'
import { PainelPasso, type Aplicar } from './PainelPasso'
import { PainelTeste } from './PainelTeste'
import { SeletorDeBloco } from './SeletorDeBloco'
import { Aviso, Confirmar, Dialog, useNotificar } from './ui'
import { Verificador } from './Verificador'

type Painel = null | 'passo' | 'adicionar' | 'teste' | 'verificador' | 'historico'

type Dialogo =
  | { tipo: 'atalhos' }
  | { tipo: 'bloco'; editar?: BlockType; modelo?: BlockType; baseVersao?: number }
  | { tipo: 'sair' }
  | { tipo: 'conflito' }
  | null

function baixar(nome: string, conteudo: string) {
  const url = URL.createObjectURL(new Blob([conteudo], { type: 'application/json' }))
  const a = document.createElement('a')
  a.href = url
  a.download = nome
  a.click()
  URL.revokeObjectURL(url)
}

const dormir = (ms: number) => new Promise((r) => window.setTimeout(r, ms))

export function EditorPage({ projectId, onSair }: { projectId: string; onSair: () => void }) {
  const notificar = useNotificar()
  const [falhaCarga, setFalhaCarga] = useState<ApiFailure | null>(null)
  const [projeto, setProjeto] = useState<Project | null>(null)
  const [nome, setNome] = useState('')
  const hist = useHistorico<Flow>()
  const flowAtual = hist.atual
  const [defs, setDefs] = useState<Map<string, BlockType>>(new Map())
  const [biblioteca, setBiblioteca] = useState<BlockType[]>([])
  const [sistema, setSistema] = useState<SystemInfo | null>(null)
  const [analise, setAnalise] = useState<{ issues: Issue[]; port_types: PortTypes }>({ issues: [], port_types: {} })
  const [verificando, setVerificando] = useState(false)
  const [salvoJson, setSalvoJson] = useState<string | null>(null)
  const [salvando, setSalvando] = useState(false)
  const [selecionadoId, setSelecionadoId] = useState<string | null>(null)
  const [painel, setPainel] = useState<Painel>(null)
  const [destino, setDestino] = useState<Destino | null>(null)
  const [run, setRun] = useState<Run | null>(null)
  const [executando, setExecutando] = useState(false)
  const [historicoRuns, setHistoricoRuns] = useState<Run[]>([])
  const [visao, setVisao] = useState<{ run: Run; flow: Flow } | null>(null)
  const [iteracoes, setIteracoes] = useState<Record<string, number>>({})
  const [dialogo, setDialogo] = useState<Dialogo>(null)
  const [iniciarTeste, setIniciarTeste] = useState(false)

  const vivo = useRef(true)
  const contadorValidacao = useRef(0)
  const estado = useRef({ flow: flowAtual, nome, projeto })
  estado.current = { flow: flowAtual, nome, projeto }
  useEffect(() => () => { vivo.current = false }, [])

  const flow = visao?.flow ?? flowAtual
  const somenteLeitura = !!visao
  const runVisivel = visao ? visao.run : run

  // -------------------------------------------------------------------------- definições dos blocos
  const defDe = useCallback((p: Passo) => defs.get(chaveDoTipo(p.type, p.version)), [defs])

  const garantirDefs = useCallback(async (f: Flow) => {
    const faltando = todosOsPassos(f).filter((p) => !defs.has(chaveDoTipo(p.type, p.version)))
    if (faltando.length === 0) return
    const achadas = new Map<string, BlockType>()
    for (const p of faltando) {
      const k = chaveDoTipo(p.type, p.version)
      if (achadas.has(k)) continue
      try { achadas.set(k, await api.versaoDoBloco(p.type, p.version)) } catch { /* bloco indisponível: o cartão avisa */ }
    }
    if (achadas.size && vivo.current) setDefs((m) => { const n = new Map(m); achadas.forEach((v, k) => n.set(k, v)); return n })
  }, [defs])

  // -------------------------------------------------------------------------- carga inicial
  useEffect(() => {
    let cancelado = false
    ;(async () => {
      try {
        const [p, blocos, sis] = await Promise.all([api.projeto(projectId), api.blocos(), api.sistema()])
        const mapa = new Map<string, BlockType>(blocos.map((b) => [chaveDoTipo(b.id, b.version), b]))
        for (const passo of todosOsPassos(p.flow)) {
          const k = chaveDoTipo(passo.type, passo.version)
          if (!mapa.has(k)) {
            try { mapa.set(k, await api.versaoDoBloco(passo.type, passo.version)) } catch { /* bloco indisponível: o cartão avisa */ }
          }
        }
        if (cancelado) return
        setProjeto(p); setNome(p.name)
        setDefs(mapa); setBiblioteca(blocos); setSistema(sis)
        hist.reiniciar(p.flow)
        setSalvoJson(JSON.stringify(p.flow))
        api.historico(p.id).then((h) => !cancelado && setHistoricoRuns(h)).catch(() => undefined)
      } catch (e) {
        if (!cancelado) setFalhaCarga(e as ApiFailure)
      }
    })()
    return () => { cancelado = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  // -------------------------------------------------------------------------- derivados
  const fluxoJson = useMemo(() => (flowAtual ? JSON.stringify(flowAtual) : ''), [flowAtual])
  const sujo = salvoJson !== null && !visao && (fluxoJson !== salvoJson || nome !== projeto?.name)

  useEffect(() => {
    const aviso = (e: BeforeUnloadEvent) => { if (sujo) { e.preventDefault(); e.returnValue = '' } }
    window.addEventListener('beforeunload', aviso)
    return () => window.removeEventListener('beforeunload', aviso)
  }, [sujo])

  // verificação contínua (o servidor é a única fonte de verdade das regras)
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
  }, [])

  useEffect(() => {
    if (salvoJson === null || visao) return
    const t = window.setTimeout(revalidar, 350)
    return () => window.clearTimeout(t)
  }, [fluxoJson, salvoJson, revalidar, sistema?.executor.disponivel, visao])

  const ultimasVersoes = useMemo(() => {
    const m = new Map<string, number>()
    for (const b of biblioteca) m.set(b.id, Math.max(m.get(b.id) ?? 0, b.version))
    return m
  }, [biblioteca])

  const erros = useMemo(() => analise.issues.filter((i) => i.severity === 'erro'), [analise.issues])
  const executorOk = sistema?.executor.disponivel ?? false
  const nomeDoId = useCallback((id: string) => {
    const p = flow ? acharQualquer(flow, id) : null
    return p ? nomeDoPasso(p, defDe(p)) : id
  }, [flow, defDe])

  const camposDoGatilho = useMemo(() => {
    if (!flowAtual) return []
    const d = defDe(flowAtual.trigger)
    return d ? definicaoEfetiva(d, flowAtual.trigger.params).outputs : []
  }, [flowAtual, defDe])

  // -------------------------------------------------------------------------- edição do fluxo
  const aplicar: Aplicar = useCallback((fn, coalescer) => hist.definir(fn, coalescer), [hist])

  function abrirSeletor(d: Destino) {
    if (!flow) return
    setDestino(d)
    setPainel('adicionar')
  }

  function escolherBloco(def: BlockType) {
    if (!destino) return
    const novo = novoPasso(def)
    hist.definir((f) => inserir(f, destino, novo))
    setDefs((m) => (m.has(chaveDoTipo(def.id, def.version)) ? m : new Map(m).set(chaveDoTipo(def.id, def.version), def)))
    setSelecionadoId(novo.id)
    setPainel('passo')
    setDestino(null)
    notificar.anunciar(`Passo ${def.name} adicionado.`)
  }

  function acao(id: string, a: AcaoDoPasso) {
    if (!flowAtual) return
    const nomePasso = nomeDoId(id)
    if (a === 'duplicar') {
      const r = duplicar(flowAtual, id)
      hist.definir(r.flow)
      if (r.novoId) { setSelecionadoId(r.novoId); setPainel('passo') }
      notificar.anunciar(`Passo ${nomePasso} duplicado.`)
    } else if (a === 'excluir') {
      const usado = usaOPasso(flowAtual, id)
      hist.definir((f) => remover(f, id))
      if (selecionadoId === id || (selecionadoId && !achar(remover(flowAtual, id), selecionadoId))) { setSelecionadoId(null); setPainel((p) => (p === 'passo' ? null : p)) }
      notificar.info(`Passo “${nomePasso}” excluído`, usado ? 'Outros passos usavam o conteúdo dele: veja o verificador de fluxo. Ctrl+Z desfaz.' : 'Ctrl+Z desfaz.')
    } else {
      hist.definir((f) => mover(f, id, a === 'subir' ? -1 : 1))
      notificar.anunciar(`Passo ${nomePasso} movido ${a === 'subir' ? 'para cima' : 'para baixo'}.`)
    }
  }

  function selecionar(id: string) {
    setSelecionadoId(id)
    setPainel('passo')
    setDestino(null)
  }

  const irParaPasso = useCallback((id: string) => {
    setSelecionadoId(id)
    setPainel('passo')
    window.requestAnimationFrame(() => {
      const el = document.querySelector<HTMLElement>(`[data-passo="${id}"]`)
      el?.scrollIntoView({ block: 'center', behavior: 'smooth' })
      el?.querySelector<HTMLElement>('.cartao-principal')?.focus()
    })
  }, [])

  function atualizarVersao(p: Passo) {
    const ultima = ultimasVersoes.get(p.type)
    const nova = ultima ? defs.get(chaveDoTipo(p.type, ultima)) : undefined
    if (!ultima || !nova) return
    hist.definir((f) => ({ ...f, steps: JSON.parse(JSON.stringify(f.steps), (k, v) => (v && typeof v === 'object' && v.id === p.id && v.type === p.type ? { ...v, version: ultima } : v)) }))
    notificar.info('Bloco atualizado', 'Confira o verificador de fluxo: se os campos mudaram, algum passo pode precisar de ajuste.')
  }

  // -------------------------------------------------------------------------- salvar / exportar
  const salvar = useCallback(async (): Promise<boolean> => {
    const { flow: f, nome: n, projeto: p } = estado.current
    if (!p || !f || salvando || visao) return false
    setSalvando(true)
    try {
      const salvo = await api.salvarProjeto(p.id, { name: n.trim() || p.name, flow: f, base_revision: p.revision })
      setProjeto(salvo)
      setNome(salvo.name)
      setSalvoJson(JSON.stringify(f))
      notificar.sucesso('Fluxo salvo')
      void revalidar()
      return true
    } catch (e) {
      const x = e as ApiFailure
      if (x.code === 'conflito_de_revisao') setDialogo({ tipo: 'conflito' })
      else {
        notificar.erro('Não foi possível salvar', `${x.message} ${x.issues[0]?.message ?? ''}`.trim())
        if (x.issues.length) { setAnalise((a) => ({ ...a, issues: x.issues })); setPainel('verificador') }
      }
      return false
    } finally {
      setSalvando(false)
    }
  }, [salvando, visao, notificar, revalidar])

  async function exportar() {
    if (!flow) return
    try {
      const dados = await api.exportarFluxo(nome, projeto?.description ?? '', flow)
      baixar(`${slugDeId(nome) || 'fluxo'}.trama.json`, JSON.stringify(dados, null, 2))
      notificar.sucesso('Fluxo exportado', 'O arquivo inclui os blocos Python usados, nas versões fixadas.')
    } catch (e) {
      notificar.erro('Não foi possível exportar', (e as ApiFailure).message)
    }
  }

  // -------------------------------------------------------------------------- execução
  const acompanhar = useCallback(async (id: string) => {
    let anterior = ''
    try {
      while (vivo.current) {
        const r = await api.execucao(id)
        setRun(r)
        const atual = r.steps.find((s) => s.state === 'executando')
        if (atual && atual.step_id !== anterior) {
          anterior = atual.step_id
          const p = estado.current.flow ? acharQualquer(estado.current.flow, atual.step_id) : null
          notificar.anunciar(`Executando ${p ? nomeDoPasso(p, undefined) : 'passo'}.`)
        }
        if (['concluido', 'falhou', 'cancelado'].includes(r.state)) {
          notificar.anunciar(r.state === 'concluido' ? `Teste concluído em ${r.duration_ms} milissegundos.`
            : r.state === 'cancelado' ? 'A execução foi cancelada.' : `O teste falhou no passo ${r.error?.step_name ?? ''}: ${r.error?.message ?? ''}`)
          break
        }
        await dormir(300)
      }
    } catch (e) {
      notificar.erro('Perdemos o contato com o servidor durante o teste', (e as ApiFailure).message)
    } finally {
      if (vivo.current) {
        setExecutando(false)
        api.historico(projectId).then((h) => vivo.current && setHistoricoRuns(h)).catch(() => undefined)
      }
    }
  }, [notificar, projectId])

  const testar = useCallback(async (dados?: Record<string, unknown>) => {
    const { flow: f, projeto: p } = estado.current
    if (!p || !f || executando) return
    if (f.steps.length === 0) {
      notificar.info('O fluxo está vazio', 'Adicione ao menos um passo antes de testar.')
      return
    }
    setExecutando(true); setRun(null); setVisao(null); setIteracoes({})
    try {
      const r = await api.executar(p.id, f, dados)
      setRun(r)
      notificar.anunciar('Teste iniciado.')
      void acompanhar(r.id)
    } catch (e) {
      const x = e as ApiFailure
      setExecutando(false)
      if (x.issues.length && x.code === 'fluxo_invalido') {
        await revalidar()
        setPainel('verificador')
        notificar.erro('O fluxo não foi testado', `${x.issues.length} ${x.issues.length === 1 ? 'problema precisa' : 'problemas precisam'} ser corrigido(s). Veja o verificador de fluxo.`)
      } else notificar.erro('Não foi possível testar', [x.message, x.issues[0]?.message].filter(Boolean).join(' '))
    }
  }, [executando, acompanhar, revalidar, notificar])

  function abrirTeste(iniciar = false) {
    setIniciarTeste(iniciar)
    setPainel('teste')
    setSelecionadoId(null)
  }

  async function cancelarExecucao() {
    if (!run) return
    try { await api.cancelarExecucao(run.id) } catch (e) { notificar.erro('Não foi possível cancelar', (e as ApiFailure).message) }
  }

  async function abrirExecucao(r: Run) {
    try {
      const completa = await api.execucaoComFluxo(r.id)
      await garantirDefs(completa.flow)
      setVisao({ run: completa, flow: completa.flow })
      setIteracoes({})
      setSelecionadoId(null)
      setPainel('historico')
    } catch (e) { notificar.erro('Não foi possível abrir a execução', (e as ApiFailure).message) }
  }

  function reenviar(r: Run) {
    setVisao(null)
    abrirTeste(false)
    void testar(r.trigger_inputs)
  }

  // -------------------------------------------------------------------------- teclado
  useAtalhos({
    salvar: () => void salvar(),
    testar: () => abrirTeste(true),
    desfazer: hist.desfazer,
    refazer: hist.refazer,
    fechar: () => {
      setDestino(null)
      setPainel((p) => (p === 'adicionar' || p === 'passo' ? null : p))
    },
  })

  // -------------------------------------------------------------------------- blocos Python reutilizáveis
  async function aposSalvarBloco(b: BlockType, avisos: string[]) {
    try {
      const lista = await api.blocos()
      setBiblioteca(lista)
      setDefs((m) => {
        const n = new Map(m)
        for (const x of lista) n.set(chaveDoTipo(x.id, x.version), x)
        n.set(chaveDoTipo(b.id, b.version), b)
        return n
      })
    } catch { /* a biblioteca se atualiza na próxima carga */ }
    notificar.sucesso(`Bloco “${b.name}” salvo na biblioteca (v${b.version})`,
      avisos[0] ?? 'Use-o em qualquer fluxo pelo seletor de passos. Fluxos que usam uma versão anterior continuam nela.')
  }

  function blocoDoPasso(p: Passo): BlockType | undefined {
    const d = defDe(p)
    if (!d) return undefined
    const ef = definicaoEfetiva(d, p.params)
    return { ...d, id: 'custom.novo', name: p.label || 'Meu bloco Python', description: '', category: 'Personalizados', kind: 'python', code: String(p.params.codigo ?? d.params.find((x) => x.id === 'codigo')?.default ?? ''), inputs: ef.inputs, outputs: ef.outputs, params: [] }
  }

  // -------------------------------------------------------------------------- telas de apoio
  if (falhaCarga) {
    return (
      <div className="tela-cheia-centro">
        <Aviso tipo="erro" titulo={falhaCarga.status === 404 ? 'Fluxo não encontrado' : 'Não foi possível abrir o fluxo'}>
          {falhaCarga.message}
          <div className="acoes-linha"><button className="btn btn-primario" onClick={onSair}>Voltar aos fluxos</button></div>
        </Aviso>
      </div>
    )
  }
  if (!projeto || !flow || !flowAtual) return <div className="tela-cheia-centro"><p role="status">Abrindo o fluxo…</p></div>

  const selecionado = selecionadoId ? acharQualquer(flow, selecionadoId) : null
  const posSelecionado = selecionado && selecionado.id !== flow.trigger.id ? achar(flow, selecionado.id) : null
  const ctxSelecionado = selecionado ? contextoDoPasso(flow, selecionado.id, runVisivel, iteracoes) : []
  const linhaSelecionada = selecionado ? linhaDoPasso(runVisivel, selecionado.id, ctxSelecionado) : null
  const nomeDestino = destino
    ? (destino.paiId ? `Dentro de “${nomeDoId(destino.paiId)}”${destino.espaco ? ` (${defDe(acharQualquer(flow, destino.paiId)!)?.slots.find((s) => s.id === destino.espaco)?.label ?? destino.espaco})` : ''}`
      : destino.indice === 0 ? 'Logo depois do gatilho' : `Depois de “${nomeDoId(flow.steps[destino.indice - 1].id)}”`)
    : ''
  const painelAtual: Painel = visao && painel === 'passo' && !selecionado ? 'historico' : painel

  return (
    <div className="editor">
      <a className="pular-link" href="#area-trabalho">Ir para o fluxo</a>
      <header className="barra" role="banner">
        <button className="btn btn-fantasma" onClick={() => (sujo ? setDialogo({ tipo: 'sair' }) : onSair())}>
          <Icon name="home" size={16} /> Meus fluxos
        </button>
        <span className="barra-marca"><Logo size={26} /><span className="marca-nome">Trama</span></span>
        <input className="nome-projeto" aria-label="Nome do fluxo" value={nome} maxLength={120} disabled={somenteLeitura} onChange={(e) => setNome(e.target.value)} />
        <span className={`status-salvo ${sujo ? 'sujo' : ''}`} role="status">
          <Icon name={sujo ? 'edit' : 'check'} size={14} /> {somenteLeitura ? 'Somente leitura' : salvando ? 'Salvando…' : sujo ? 'Alterações não salvas' : 'Tudo salvo'}
        </span>
        <div className="barra-acoes">
          <button className="btn-icone" aria-label="Desfazer (Ctrl+Z)" title="Desfazer (Ctrl+Z)" disabled={!hist.podeDesfazer || somenteLeitura} onClick={hist.desfazer}><Icon name="undo" /></button>
          <button className="btn-icone" aria-label="Refazer (Ctrl+Y)" title="Refazer (Ctrl+Y)" disabled={!hist.podeRefazer || somenteLeitura} onClick={hist.refazer}><Icon name="redo" /></button>
          <button className={`btn ${painelAtual === 'verificador' ? 'btn-ativo' : ''}`} aria-pressed={painelAtual === 'verificador'} disabled={somenteLeitura}
            onClick={() => setPainel((p) => (p === 'verificador' ? null : 'verificador'))} title="Verificador de fluxo">
            <Icon name="checker" size={16} /> Verificador
            {erros.length > 0 && <span className="ponto-vermelho" aria-label={`${erros.length} ${erros.length === 1 ? 'erro' : 'erros'}`}>{erros.length}</span>}
          </button>
          <button className={`btn ${painelAtual === 'historico' ? 'btn-ativo' : ''}`} aria-pressed={painelAtual === 'historico'}
            onClick={() => setPainel((p) => (p === 'historico' ? null : 'historico'))} title="Histórico de execuções">
            <Icon name="history" size={16} /> Histórico
          </button>
          <button className="btn" onClick={() => void exportar()} title="Baixar o fluxo como arquivo JSON"><Icon name="download" size={16} /> Exportar</button>
          <button className="btn" onClick={() => void salvar()} disabled={salvando || !sujo} title="Salvar (Ctrl+S)"><Icon name="save" size={16} /> Salvar</button>
          <button className="btn btn-primario" onClick={() => abrirTeste(false)} title="Testar o fluxo (Ctrl+Enter)">
            <Icon name="play" size={16} /> {executando ? 'Testando…' : 'Testar'}
          </button>
          <button className="btn-icone" aria-label="Ver atalhos de teclado" title="Atalhos de teclado" onClick={() => setDialogo({ tipo: 'atalhos' })}><Icon name="keyboard" /></button>
        </div>
      </header>

      {sistema && !sistema.executor.disponivel && (
        <div className="banner-executor">
          <Aviso tipo="aviso" titulo="Código Python está desabilitado">
            {sistema.executor.mensagem} {sistema.executor.instrucao} Os demais blocos continuam funcionando.
          </Aviso>
        </div>
      )}
      {visao && (
        <div className="banner-visao" role="status">
          <Icon name="history" size={16} />
          <span>Você está vendo uma execução antiga, com o fluxo como ele era naquele momento. Nada aqui pode ser alterado.</span>
          <button className="btn btn-pequeno" onClick={() => { setVisao(null); setIteracoes({}) }}>Voltar ao fluxo atual</button>
        </div>
      )}

      <div className="editor-corpo">
        <main id="area-trabalho" className="area-trabalho" aria-label="Fluxo" tabIndex={-1}
          onClick={(e) => { if (e.target === e.currentTarget) { setSelecionadoId(null); setPainel((p) => (p === 'passo' ? null : p)) } }}>
          <ErrorBoundary titulo="Não foi possível desenhar o fluxo" resetKey={fluxoJson}>
            <Designer
              flow={flow} defDe={defDe} selecionadoId={selecionadoId} destinoAtivo={painelAtual === 'adicionar' ? destino : null}
              onSelecionar={selecionar} onAdicionar={abrirSeletor} onAcao={acao} problemas={somenteLeitura ? [] : analise.issues}
              run={runVisivel} somenteLeitura={somenteLeitura} iteracoes={iteracoes}
              onIteracao={(k, n) => setIteracoes((m) => ({ ...m, [k]: n }))} ultimasVersoes={ultimasVersoes}
            />
          </ErrorBoundary>
        </main>

        <ErrorBoundary titulo="Não foi possível exibir este painel" resetKey={`${painelAtual}-${selecionadoId}`}>
          {painelAtual === 'adicionar' && destino && (
            <SeletorDeBloco
              blocos={biblioteca} executorOk={executorOk} contexto={nomeDestino}
              profundidadeMaxima={profundidadeDoDestino(flow, destino) >= MAX_PROFUNDIDADE}
              onEscolher={escolherBloco} onFechar={() => { setPainel(null); setDestino(null) }}
              onNovoBlocoPython={() => setDialogo({ tipo: 'bloco' })}
              onEditarBloco={(b) => setDialogo({ tipo: 'bloco', editar: b, baseVersao: b.version })}
            />
          )}
          {painelAtual === 'passo' && selecionado && (
            <PainelPasso
              passo={selecionado} def={defDe(selecionado)} flow={flow} defDe={defDe} portTypes={somenteLeitura ? {} : analise.port_types}
              problemas={somenteLeitura ? [] : analise.issues} linha={linhaSelecionada}
              iteracaoTexto={ctxSelecionado.length ? `Repetição ${ctxSelecionado.map((n) => n + 1).join('.')}` : ''}
              somenteLeitura={somenteLeitura} indice={posSelecionado?.indice ?? 0} sistema={sistema}
              ultimaVersao={ultimasVersoes.get(selecionado.type) ?? null} aplicar={aplicar}
              onFechar={() => { setPainel(null); setSelecionadoId(null) }}
              onSalvarComoBloco={(p) => setDialogo({ tipo: 'bloco', modelo: blocoDoPasso(p) })}
              onAtualizarVersao={atualizarVersao}
              onEditarBloco={(p) => { const d = defDe(p); if (d) setDialogo({ tipo: 'bloco', editar: d, baseVersao: d.version }) }}
            />
          )}
          {painelAtual === 'teste' && (
            <PainelTeste
              campos={camposDoGatilho} run={run} executando={executando} historico={historicoRuns}
              executorMsg={sistema && !sistema.executor.disponivel ? [sistema.executor.mensagem, sistema.executor.instrucao].filter(Boolean).join(' ') : null}
              onTestar={(d) => void testar(d)} onCancelar={() => void cancelarExecucao()} onFechar={() => setPainel(null)}
              onVerNoFluxo={irParaPasso} iniciarAoAbrir={iniciarTeste}
            />
          )}
          {painelAtual === 'verificador' && (
            <Verificador problemas={analise.issues} nomeDe={nomeDoId} onIr={irParaPasso} onFechar={() => setPainel(null)} verificando={verificando} />
          )}
          {painelAtual === 'historico' && (
            <Historico runs={historicoRuns.filter((h) => h.kind === 'fluxo')} abertoId={visao?.run.id ?? null} onAbrir={(r) => void abrirExecucao(r)}
              onReenviar={reenviar} onFechar={() => setPainel(null)} ocupado={executando} />
          )}
        </ErrorBoundary>
      </div>

      {dialogo?.tipo === 'atalhos' && <AtalhosDialog onClose={() => setDialogo(null)} />}
      {dialogo?.tipo === 'bloco' && (
        <BlockEditorDialog
          editar={dialogo.editar} modelo={dialogo.modelo} baseVersao={dialogo.baseVersao} categorias={[...new Set(biblioteca.map((b) => b.category))]}
          executorOk={executorOk} onClose={() => setDialogo(null)}
          onSalvo={(b, avisos) => { setDialogo(null); void aposSalvarBloco(b, avisos) }}
        />
      )}
      {dialogo?.tipo === 'sair' && (
        <Dialog titulo="Sair sem salvar?" onClose={() => setDialogo(null)} largura={480}
          rodape={<>
            <button className="btn" onClick={() => setDialogo(null)} data-autofocus>Continuar editando</button>
            <button className="btn btn-perigo" onClick={onSair}>Sair sem salvar</button>
            <button className="btn btn-primario" onClick={async () => { if (await salvar()) onSair() }}>Salvar e sair</button>
          </>}>
          <p>Há alterações que ainda não foram salvas neste fluxo.</p>
        </Dialog>
      )}
      {dialogo?.tipo === 'conflito' && (
        <Confirmar titulo="Este fluxo mudou em outra aba" rotuloConfirmar="Exportar meu fluxo" perigo={false}
          mensagem="Outra aba ou janela salvou este fluxo depois que você o abriu. Para não perder o seu trabalho, exporte o fluxo atual e depois recarregue a página para ver a versão salva."
          onConfirmar={() => { void exportar(); setDialogo(null) }} onCancelar={() => setDialogo(null)} />
      )}
    </div>
  )
}
