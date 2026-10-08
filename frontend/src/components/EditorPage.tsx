import {
  Background, BackgroundVariant, MarkerType, MiniMap, Panel, ReactFlow, ReactFlowProvider, SelectionMode,
  useEdgesState, useNodesState, useReactFlow,
} from '@xyflow/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiFailure } from '../api'
import {
  aleatorio, chaveDoTipo, conexaoDe, deFluxo, duplicar, paraFluxo, paramsIniciais, valorPadraoDoTipo,
  type BlockNode, type FlowEdge,
} from '../lib/flow'
import { corDaCategoria, slugDeId } from '../lib/visual'
import type { BlockType, Issue, PortTypes, Project, Run, SystemInfo } from '../types'
import { BlockEditorDialog } from './BlockEditorDialog'
import { ErrorBoundary } from './ErrorBoundary'
import { BlockNodeComponent, descricaoDoNo } from './BlockNode'
import { BottomPanel, type Aba } from './BottomPanel'
import { ConfigPanel, type AcoesConfig } from './ConfigPanel'
import { AtalhosDialog, ExecutarComDadosDialog, TestarBlocoDialog } from './Dialogs'
import { Icon, Logo } from './Icons'
import { Library, MIME_BLOCO } from './Library'
import { Aviso, Confirmar, Dialog, useNotificar } from './ui'

const TIPOS_DE_NO = { block: BlockNodeComponent }

const ROTULOS_RF = {
  'node.a11yDescription.default':
    'Pressione Enter ou Espaço para selecionar este bloco. Use as setas para movê-lo depois de selecionado. Pressione Esc para limpar a seleção.',
  'node.a11yDescription.keyboardDisabled': 'Os blocos não podem ser movidos pelo teclado agora.',
  'node.a11yDescription.ariaLiveMessage': ({ direction, x, y }: { direction: string; x: number; y: number }) =>
    `Bloco movido para ${direction === 'up' ? 'cima' : direction === 'down' ? 'baixo' : direction === 'left' ? 'a esquerda' : 'a direita'}. Posição ${Math.round(x)}, ${Math.round(y)}.`,
  'edge.a11yDescription.default': 'Conexão entre blocos. Pressione Delete para removê-la depois de selecionada.',
  'controls.ariaLabel': 'Controles de zoom',
  'controls.zoomIn.ariaLabel': 'Aumentar o zoom',
  'controls.zoomOut.ariaLabel': 'Diminuir o zoom',
  'controls.fitView.ariaLabel': 'Enquadrar todos os blocos',
  'controls.interactive.ariaLabel': 'Alternar interatividade',
  'minimap.ariaLabel': 'Minimapa da área de trabalho',
  'handle.ariaLabel': 'Porta de conexão',
}

type Dialogo =
  | { tipo: 'testar'; id: string }
  | { tipo: 'executar-dados' }
  | { tipo: 'atalhos' }
  | { tipo: 'bloco'; editar?: BlockType; baseVersao?: number }
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

function lerPreferencia<T>(chave: string, padrao: T): T {
  try {
    const v = localStorage.getItem(chave)
    return v === null ? padrao : (JSON.parse(v) as T)
  } catch { return padrao }
}
function gravarPreferencia(chave: string, valor: unknown) {
  try { localStorage.setItem(chave, JSON.stringify(valor)) } catch { /* armazenamento indisponível: ignora */ }
}

export function EditorPage({ projectId, onSair }: { projectId: string; onSair: () => void }) {
  return (
    <ReactFlowProvider>
      <EditorInterno projectId={projectId} onSair={onSair} />
    </ReactFlowProvider>
  )
}

function EditorInterno({ projectId, onSair }: { projectId: string; onSair: () => void }) {
  const rf = useReactFlow<BlockNode, FlowEdge>()
  const notificar = useNotificar()
  const [falhaCarga, setFalhaCarga] = useState<ApiFailure | null>(null)
  const [projeto, setProjeto] = useState<Project | null>(null)
  const [nome, setNome] = useState('')
  const [descricao, setDescricao] = useState('')
  const [nodes, setNodes, onNodesChange] = useNodesState<BlockNode>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<FlowEdge>([])
  const [defs, setDefs] = useState<Map<string, BlockType>>(new Map())
  const [biblioteca, setBiblioteca] = useState<BlockType[]>([])
  const [sistema, setSistema] = useState<SystemInfo | null>(null)
  const [analise, setAnalise] = useState<{ issues: Issue[]; port_types: PortTypes }>({ issues: [], port_types: {} })
  const [salvoJson, setSalvoJson] = useState<string | null>(null)
  const [salvando, setSalvando] = useState(false)
  const [run, setRun] = useState<Run | null>(null)
  const [executando, setExecutando] = useState(false)
  const [historico, setHistorico] = useState<Run[]>([])
  const [aba, setAba] = useState<Aba>('resultado')
  const [painelAberto, setPainelAberto] = useState(() => lerPreferencia('trama.inferior.aberto', true))
  const [altura, setAltura] = useState(() => lerPreferencia('trama.inferior.altura', 280))
  const [mostrarBib, setMostrarBib] = useState(true)
  const [mostrarConfig, setMostrarConfig] = useState(true)
  const [modoSelecao, setModoSelecao] = useState(false)
  const [dialogo, setDialogo] = useState<Dialogo>(null)
  const [viewportInicial, setViewportInicial] = useState<{ x: number; y: number; zoom: number } | null>(null)
  // O enquadramento automático só vale para projetos que já abrem com blocos. Num projeto vazio ele
  // reenquadraria a tela ao inserir o primeiro bloco, que então "pularia" do ponto onde foi solto.
  const [abriuComBlocos, setAbriuComBlocos] = useState(false)

  const vivo = useRef(true)
  const estado = useRef({ nodes, edges })
  estado.current = { nodes, edges }
  const contadorValidacao = useRef(0)

  useEffect(() => () => { vivo.current = false }, [])
  useEffect(() => gravarPreferencia('trama.inferior.aberto', painelAberto), [painelAberto])
  useEffect(() => gravarPreferencia('trama.inferior.altura', altura), [altura])

  // -------------------------------------------------------------------------- carga inicial
  useEffect(() => {
    let cancelado = false
    ;(async () => {
      try {
        const [p, blocos, sis] = await Promise.all([api.projeto(projectId), api.blocos(), api.sistema()])
        const mapa = new Map<string, BlockType>(blocos.map((b) => [chaveDoTipo(b.id, b.version), b]))
        for (const b of p.flow.blocks) {
          const k = chaveDoTipo(b.type, b.version)
          if (!mapa.has(k)) {
            try { mapa.set(k, await api.versaoDoBloco(b.type, b.version)) } catch { /* bloco indisponível: o nó avisa */ }
          }
        }
        if (cancelado) return
        const { nodes: ns, edges: es } = deFluxo(p.flow, mapa)
        setProjeto(p); setNome(p.name); setDescricao(p.description)
        setDefs(mapa); setBiblioteca(blocos); setSistema(sis)
        setNodes(ns); setEdges(es)
        setViewportInicial(p.flow.viewport)
        setAbriuComBlocos(p.flow.blocks.length > 0)
        setSalvoJson(JSON.stringify(paraFluxo(ns, es)))
        api.historico(p.id).then((h) => !cancelado && setHistorico(h)).catch(() => undefined)
      } catch (e) {
        if (!cancelado) setFalhaCarga(e as ApiFailure)
      }
    })()
    return () => { cancelado = true }
  }, [projectId, setNodes, setEdges])

  // -------------------------------------------------------------------------- derivados
  const fluxoJson = useMemo(() => JSON.stringify(paraFluxo(nodes, edges)), [nodes, edges])
  const sujo = salvoJson !== null && (fluxoJson !== salvoJson || nome !== projeto?.name || descricao !== projeto?.description)

  useEffect(() => {
    const aviso = (e: BeforeUnloadEvent) => { if (sujo) { e.preventDefault(); e.returnValue = '' } }
    window.addEventListener('beforeunload', aviso)
    return () => window.removeEventListener('beforeunload', aviso)
  }, [sujo])

  // validação contínua (o servidor é a única fonte de verdade das regras)
  const revalidar = useCallback(async () => {
    const minha = ++contadorValidacao.current
    try {
      const r = await api.validar(JSON.parse(JSON.stringify(paraFluxo(estado.current.nodes, estado.current.edges))))
      if (vivo.current && minha === contadorValidacao.current) setAnalise({ issues: r.issues, port_types: r.port_types })
    } catch { /* sem conexão: mantém a última análise */ }
  }, [])

  useEffect(() => {
    if (salvoJson === null) return
    const t = window.setTimeout(revalidar, 350)
    return () => window.clearTimeout(t)
  }, [fluxoJson, salvoJson, revalidar, sistema?.executor.disponivel])

  const ultimasVersoes = useMemo(() => {
    const m = new Map<string, number>()
    for (const b of biblioteca) m.set(b.id, Math.max(m.get(b.id) ?? 0, b.version))
    return m
  }, [biblioteca])

  // dados derivados dentro dos nós (estado da execução, problemas, tipos efetivos, versões)
  useEffect(() => {
    const passos = new Map(run?.steps.map((s) => [s.block_id, s]))
    const erros = new Map<string, number>()
    for (const i of analise.issues) if (i.severity === 'erro' && i.block_id) erros.set(i.block_id, (erros.get(i.block_id) ?? 0) + 1)
    setNodes((ns) => ns.map((n) => {
      const def = defs.get(chaveDoTipo(n.data.block.type, n.data.block.version)) ?? null
      const step = passos.get(n.id) ?? null
      const problemCount = erros.get(n.id) ?? 0
      const portTypes = analise.port_types[n.id]
      const latestVersion = def?.kind === 'python' ? ultimasVersoes.get(def.id) ?? null : null
      if (n.data.step === step && n.data.problemCount === problemCount && n.data.def === def
        && JSON.stringify(n.data.portTypes) === JSON.stringify(portTypes) && n.data.latestVersion === latestVersion) return n
      return {
        ...n,
        ariaLabel: descricaoDoNo(n.data.block.label || def?.name || n.data.block.type, step?.state, problemCount),
        data: { ...n.data, step, def, problemCount, portTypes, latestVersion },
      }
    }))
  }, [run, analise, defs, ultimasVersoes, setNodes])

  useEffect(() => {
    const passos = new Map(run?.steps.map((s) => [s.block_id, s]))
    setEdges((es) => es.map((e) => {
      const tipo = analise.port_types[e.source]?.outputs[e.sourceHandle ?? ''] ?? 'qualquer'
      const origem = passos.get(e.source)
      const destino = passos.get(e.target)
      const ignorado = origem?.state === 'ignorado' || destino?.state === 'ignorado'
        || (origem?.state === 'concluido' && origem.outputs !== null && e.sourceHandle != null && !(e.sourceHandle in origem.outputs))
      const className = `fio fio-${tipo}${ignorado ? ' fio-ignorado' : ''}`
      const animated = destino?.state === 'executando'
      if (e.className === className && e.animated === animated) return e
      return { ...e, className, animated, markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: '#4A5263' } }
    }))
  }, [analise.port_types, run, setEdges])

  const selecionados = useMemo(() => nodes.filter((n) => n.selected), [nodes])
  const problemasDeErro = useMemo(() => analise.issues.filter((i) => i.severity === 'erro'), [analise.issues])
  const nomes = useMemo(
    () => Object.fromEntries(nodes.map((n) => [n.id, n.data.block.label || n.data.def?.name || n.data.block.type])),
    [nodes],
  )
  const executorOk = sistema?.executor.disponivel ?? false

  // -------------------------------------------------------------------------- edição do fluxo
  const adicionar = useCallback((def: BlockType, posicao?: { x: number; y: number }) => {
    let pos = posicao
    if (!pos) {
      const caixa = document.getElementById('area-trabalho')?.getBoundingClientRect()
      const centro = caixa
        ? rf.screenToFlowPosition({ x: caixa.left + caixa.width / 2, y: caixa.top + caixa.height / 2 })
        : { x: 0, y: 0 }
      const desloc = (estado.current.nodes.length % 6) * 28
      pos = { x: centro.x - 110 + desloc, y: centro.y - 60 + desloc }
    }
    const id = aleatorio('blk')
    const novo: BlockNode = {
      id, type: 'block', position: { x: Math.round(pos.x / 16) * 16, y: Math.round(pos.y / 16) * 16 }, selected: true,
      data: { block: { id, type: def.id, version: def.version, params: paramsIniciais(def), label: null }, def, problemCount: 0 },
    }
    setNodes((ns) => [...ns.map((n) => ({ ...n, selected: false })), novo])
    setDefs((m) => (m.has(chaveDoTipo(def.id, def.version)) ? m : new Map(m).set(chaveDoTipo(def.id, def.version), def)))
    notificar.anunciar(`Bloco ${def.name} adicionado à área de trabalho.`)
  }, [rf, setNodes, notificar])

  function aoSoltar(e: React.DragEvent) {
    const bruto = e.dataTransfer.getData(MIME_BLOCO)
    if (!bruto) return
    e.preventDefault()
    const { id, version } = JSON.parse(bruto) as { id: string; version: number }
    const def = biblioteca.find((b) => b.id === id && b.version === version)
    if (def) adicionar(def, rf.screenToFlowPosition({ x: e.clientX, y: e.clientY }))
  }

  const conectar = useCallback(async (origem: { block: string; port: string }, destino: { block: string; port: string }) => {
    const { nodes: ns, edges: es } = estado.current
    const substituidas = es.filter((e) => e.target === destino.block && e.targetHandle === destino.port)
    const restantes = es.filter((e) => !substituidas.includes(e))
    const candidata = conexaoDe({ source: origem.block, sourceHandle: origem.port, target: destino.block, targetHandle: destino.port })
    try {
      const r = await api.validarConexao(paraFluxo(ns, restantes), candidata)
      if (!r.ok) {
        const i = r.issues[0]
        notificar.erro('Esta conexão não é permitida', `${i.message}${i.hint ? ' ' + i.hint : ''}`)
        return
      }
    } catch (e) {
      notificar.erro('Não foi possível validar a conexão', (e as ApiFailure).message)
      return
    }
    setEdges((atuais) => [
      ...atuais.filter((e) => !(e.target === destino.block && e.targetHandle === destino.port)),
      { id: candidata.id, source: origem.block, sourceHandle: origem.port, target: destino.block, targetHandle: destino.port, data: {} },
    ])
    notificar.anunciar(`Conectado: ${nomes[origem.block] ?? 'bloco'} para ${nomes[destino.block] ?? 'bloco'}.`)
  }, [nomes, notificar, setEdges])

  function alterarParam(id: string, paramId: string, valor: unknown) {
    setNodes((ns) => ns.map((n) => {
      if (n.id !== id || !n.data.def) return n
      const params = { ...n.data.block.params, [paramId]: valor }
      for (const p of n.data.def.params) {
        // ex.: ao trocar o "tipo" da constante, o valor volta ao padrão do novo tipo
        if (p.type_from?.param === paramId) params[p.id] = valorPadraoDoTipo(String(valor))
      }
      return { ...n, data: { ...n.data, block: { ...n.data.block, params } } }
    }))
  }

  function alterarRotulo(id: string, rotulo: string) {
    setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, data: { ...n.data, block: { ...n.data.block, label: rotulo || null } } } : n)))
  }

  function duplicarSelecionados() {
    const ids = new Set(estado.current.nodes.filter((n) => n.selected).map((n) => n.id))
    if (ids.size === 0) return
    const r = duplicar(estado.current.nodes, estado.current.edges, ids)
    setNodes((ns) => [...ns.map((n) => ({ ...n, selected: false })), ...r.nodes])
    setEdges((es) => [...es, ...r.edges])
    notificar.anunciar(`${r.nodes.length} ${r.nodes.length === 1 ? 'bloco duplicado' : 'blocos duplicados'}.`)
  }

  function excluirSelecionados() {
    const ns = estado.current.nodes.filter((n) => n.selected)
    if (ns.length === 0) return
    rf.deleteElements({ nodes: ns.map((n) => ({ id: n.id })) })
    notificar.anunciar(`${ns.length} ${ns.length === 1 ? 'bloco excluído' : 'blocos excluídos'}.`)
  }

  function atualizarVersao(id: string) {
    setNodes((ns) => ns.map((n) => {
      const ultima = n.data.def ? ultimasVersoes.get(n.data.def.id) : undefined
      const nova = ultima ? defs.get(chaveDoTipo(n.data.def!.id, ultima)) : undefined
      if (n.id !== id || !nova) return n
      return { ...n, data: { ...n.data, def: nova, block: { ...n.data.block, version: nova.version } } }
    }))
    notificar.info('Bloco atualizado', 'Confira a aba “Problemas”: se as portas mudaram, algumas conexões podem precisar de ajuste.')
  }

  const irParaBloco = useCallback((id: string) => {
    setNodes((ns) => ns.map((n) => ({ ...n, selected: n.id === id })))
    rf.fitView({ nodes: [{ id }], duration: 300, maxZoom: 1.1 })
    window.requestAnimationFrame(() => document.querySelector<HTMLElement>(`.react-flow__node[data-id="${id}"]`)?.focus())
  }, [rf, setNodes])

  // -------------------------------------------------------------------------- salvar / exportar
  const salvar = useCallback(async (): Promise<boolean> => {
    if (!projeto || salvando) return false
    setSalvando(true)
    const { nodes: ns, edges: es } = estado.current
    try {
      const p = await api.salvarProjeto(projeto.id, {
        name: nome.trim() || projeto.name, description: descricao,
        flow: paraFluxo(ns, es, rf.getViewport()), base_revision: projeto.revision,
      })
      setProjeto(p)
      setNome(p.name)
      setSalvoJson(JSON.stringify(paraFluxo(ns, es)))
      notificar.sucesso('Projeto salvo')
      return true
    } catch (e) {
      const f = e as ApiFailure
      if (f.code === 'conflito_de_revisao') setDialogo({ tipo: 'conflito' })
      else {
        notificar.erro('Não foi possível salvar', `${f.message} ${f.issues[0]?.message ?? ''}`.trim())
        if (f.issues.length) { setAnalise((a) => ({ ...a, issues: f.issues })); setAba('problemas'); setPainelAberto(true) }
      }
      return false
    } finally {
      setSalvando(false)
    }
  }, [projeto, salvando, nome, descricao, rf, notificar])

  async function exportar() {
    try {
      const dados = await api.exportarFluxo(nome, descricao, paraFluxo(estado.current.nodes, estado.current.edges, rf.getViewport()))
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
        if (atual && atual.block_id !== anterior) {
          anterior = atual.block_id
          notificar.anunciar(`Executando ${estado.current.nodes.find((n) => n.id === atual.block_id)?.data.block.label
            || estado.current.nodes.find((n) => n.id === atual.block_id)?.data.def?.name || 'bloco'}.`)
        }
        if (r.state === 'concluido' || r.state === 'falhou') {
          notificar.anunciar(r.state === 'concluido'
            ? `Execução concluída em ${r.duration_ms} milissegundos.`
            : `A execução falhou no bloco ${r.error?.block_name ?? ''}: ${r.error?.message ?? ''}`)
          break
        }
        await dormir(300)
      }
    } catch (e) {
      notificar.erro('Perdemos o contato com o servidor durante a execução', (e as ApiFailure).message)
    } finally {
      if (vivo.current) {
        setExecutando(false)
        api.historico(projectId).then((h) => vivo.current && setHistorico(h)).catch(() => undefined)
      }
    }
  }, [notificar, projectId])

  const executar = useCallback(async (dados?: Record<string, Record<string, unknown>>) => {
    if (!projeto || executando) return
    if (estado.current.nodes.length === 0) {
      notificar.info('O fluxo está vazio', 'Adicione blocos da biblioteca antes de executar.')
      return
    }
    setExecutando(true); setRun(null); setPainelAberto(true); setAba('resultado')
    try {
      const r = await api.executar(projeto.id, paraFluxo(estado.current.nodes, estado.current.edges), dados)
      setRun(r)
      notificar.anunciar('Execução iniciada.')
      void acompanhar(r.id)
    } catch (e) {
      const f = e as ApiFailure
      setExecutando(false)
      if (f.issues.length) {
        await revalidar()
        setAba('problemas')
        notificar.erro('O fluxo não foi executado', `${f.issues.length} ${f.issues.length === 1 ? 'problema precisa' : 'problemas precisam'} ser corrigido(s). Veja a aba “Problemas”.`)
      } else {
        notificar.erro('Não foi possível executar', f.message)
      }
    }
  }, [projeto, executando, acompanhar, revalidar, notificar])

  async function abrirExecucao(id: string) {
    try { setRun(await api.execucao(id)); setAba('resultado') } catch (e) { notificar.erro('Não foi possível abrir a execução', (e as ApiFailure).message) }
  }

  // -------------------------------------------------------------------------- teclado
  useEffect(() => {
    function aoTeclar(e: KeyboardEvent) {
      if (document.querySelector('[role="dialog"]')) return
      const alvo = e.target as HTMLElement
      const emCampo = !!alvo.closest('input, textarea, select, [contenteditable="true"], .cm-editor')
      const ctrl = e.ctrlKey || e.metaKey
      if (ctrl && e.key.toLowerCase() === 's') { e.preventDefault(); void salvar(); return }
      if (ctrl && e.key === 'Enter') { e.preventDefault(); void executar(); return }
      if (emCampo) return
      if (ctrl && e.key.toLowerCase() === 'd') { e.preventDefault(); duplicarSelecionados(); return }
      if (ctrl && e.key.toLowerCase() === 'a' && alvo.closest('.react-flow')) {
        e.preventDefault()
        setNodes((ns) => ns.map((n) => ({ ...n, selected: true })))
        return
      }
      if (e.key === '/' && !ctrl) { e.preventDefault(); document.getElementById('busca-blocos')?.focus(); return }
      if (e.key === 'Escape') {
        setNodes((ns) => (ns.some((n) => n.selected) ? ns.map((n) => ({ ...n, selected: false })) : ns))
      }
    }
    window.addEventListener('keydown', aoTeclar)
    return () => window.removeEventListener('keydown', aoTeclar)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [salvar, executar])

  // -------------------------------------------------------------------------- ações do painel
  const acoes: AcoesConfig = {
    onParam: alterarParam,
    onRotulo: alterarRotulo,
    onConectar: (o, d) => void conectar(o, d),
    onDesconectar: (d) => setEdges((es) => es.filter((e) => !(e.target === d.block && e.targetHandle === d.port))),
    onTestar: (id) => setDialogo({ tipo: 'testar', id }),
    onDuplicar: duplicarSelecionados,
    onExcluir: excluirSelecionados,
    onAtualizarVersao: atualizarVersao,
    onEditarBloco: (id) => {
      const n = estado.current.nodes.find((x) => x.id === id)
      if (n?.data.def) setDialogo({ tipo: 'bloco', editar: n.data.def, baseVersao: n.data.def.version })
    },
    onVerDetalhes: () => { setAba('erros'); setPainelAberto(true) },
    onIrParaBloco: irParaBloco,
  }

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
      avisos[0] ?? 'Arraste-o para o fluxo. Fluxos que já usam uma versão anterior continuam nela.')
  }

  if (falhaCarga) {
    return (
      <div className="tela-cheia-centro">
        <Aviso tipo="erro" titulo={falhaCarga.status === 404 ? 'Projeto não encontrado' : 'Não foi possível abrir o projeto'}>
          {falhaCarga.message}
          <div className="acoes-linha"><button className="btn btn-primario" onClick={onSair}>Voltar aos projetos</button></div>
        </Aviso>
      </div>
    )
  }
  if (!projeto) return <div className="tela-cheia-centro"><p role="status">Abrindo o projeto…</p></div>

  const noTeste = dialogo?.tipo === 'testar' ? nodes.find((n) => n.id === dialogo.id) : undefined
  const inicios = nodes.filter((n) => n.data.block.type === 'builtin.inicio')

  return (
    <div className="editor">
      <a className="pular-link" href="#area-trabalho">Ir para a área de trabalho</a>
      <header className="barra" role="banner">
        <button className="btn btn-fantasma" onClick={() => (sujo ? setDialogo({ tipo: 'sair' }) : onSair())}>
          <Icon name="home" size={16} /> Projetos
        </button>
        <span className="barra-marca"><Logo size={26} /><span className="marca-nome">Trama</span></span>
        <input className="nome-projeto" aria-label="Nome do projeto" value={nome} maxLength={120} onChange={(e) => setNome(e.target.value)} />
        <span className={`status-salvo ${sujo ? 'sujo' : ''}`} role="status">
          <Icon name={sujo ? 'edit' : 'check'} size={14} /> {salvando ? 'Salvando…' : sujo ? 'Alterações não salvas' : 'Tudo salvo'}
        </span>
        <div className="barra-acoes">
          <button className="btn-icone" aria-pressed={mostrarBib} aria-label="Mostrar ou ocultar a biblioteca de blocos" title="Biblioteca"
            onClick={() => setMostrarBib((v) => !v)}><Icon name="panelLeft" /></button>
          <button className="btn-icone" aria-pressed={mostrarConfig} aria-label="Mostrar ou ocultar o painel de configuração" title="Configuração"
            onClick={() => setMostrarConfig((v) => !v)}><Icon name="panelRight" /></button>
          <button className="btn-icone" aria-label="Ver atalhos de teclado" title="Atalhos de teclado" onClick={() => setDialogo({ tipo: 'atalhos' })}><Icon name="keyboard" /></button>
          <button className="btn" onClick={exportar} title="Baixar o fluxo como arquivo JSON"><Icon name="download" size={16} /> Exportar</button>
          <button className="btn" onClick={() => void salvar()} disabled={salvando || !sujo} title="Salvar (Ctrl+S)"><Icon name="save" size={16} /> Salvar</button>
          <button className="btn" onClick={() => setDialogo({ tipo: 'executar-dados' })} disabled={executando} title="Informar os dados desta execução">Executar com dados…</button>
          <button className="btn btn-primario" onClick={() => void executar()} disabled={executando} title="Executar o fluxo (Ctrl+Enter)">
            <Icon name="play" size={16} /> {executando ? 'Executando…' : 'Executar'}
          </button>
        </div>
      </header>

      {sistema && !sistema.executor.disponivel && (
        <div className="banner-executor">
          <Aviso tipo="aviso" titulo="Código Python personalizado está desabilitado">
            {sistema.executor.mensagem} {sistema.executor.instrucao} Os demais blocos continuam funcionando.
          </Aviso>
        </div>
      )}

      <div className={`editor-corpo ${mostrarBib ? '' : 'sem-bib'} ${mostrarConfig ? '' : 'sem-config'}`}>
        {mostrarBib && (
          <Library blocos={biblioteca} executorOk={executorOk} onAdicionar={(b) => adicionar(b)}
            onNovoBlocoPython={() => setDialogo({ tipo: 'bloco' })}
            onEditarBloco={(b) => setDialogo({ tipo: 'bloco', editar: b, baseVersao: b.version })} />
        )}
        <main id="area-trabalho" className="area-trabalho" aria-label="Área de trabalho" tabIndex={-1}>
          <ReactFlow<BlockNode, FlowEdge>
            nodes={nodes} edges={edges} nodeTypes={TIPOS_DE_NO}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
            onConnect={(c) => void conectar({ block: c.source, port: c.sourceHandle ?? '' }, { block: c.target, port: c.targetHandle ?? '' })}
            isValidConnection={(c) => c.source !== c.target}
            onDrop={aoSoltar} onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy' }}
            defaultViewport={viewportInicial ?? (abriuComBlocos ? undefined : { x: 40, y: 40, zoom: 1 })}
            fitView={!viewportInicial && abriuComBlocos} fitViewOptions={{ padding: 0.12, maxZoom: 1 }}
            minZoom={0.2} maxZoom={2} snapToGrid snapGrid={[16, 16]}
            deleteKeyCode={['Delete', 'Backspace']} multiSelectionKeyCode={['Shift', 'Control', 'Meta']}
            selectionOnDrag={modoSelecao} panOnDrag={modoSelecao ? [1, 2] : true} selectionMode={SelectionMode.Partial}
            ariaLabelConfig={ROTULOS_RF} connectionRadius={28}
            defaultEdgeOptions={{ markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: '#4A5263' } }}
          >
            <Background variant={BackgroundVariant.Lines} gap={32} lineWidth={1} color="#E4DAC5" />
            <Panel position="top-left" className="ferramentas-canvas">
              <div role="toolbar" aria-label="Ferramentas da área de trabalho">
                <button className="btn-icone" onClick={() => rf.zoomIn({ duration: 150 })} aria-label="Aumentar o zoom" title="Aumentar o zoom"><Icon name="zoomIn" /></button>
                <button className="btn-icone" onClick={() => rf.zoomOut({ duration: 150 })} aria-label="Diminuir o zoom" title="Diminuir o zoom"><Icon name="zoomOut" /></button>
                <button className="btn-icone" onClick={() => rf.fitView({ duration: 250, padding: 0.12, maxZoom: 1 })} aria-label="Enquadrar todos os blocos" title="Enquadrar todos os blocos"><Icon name="fit" /></button>
                <button className="btn-icone" aria-pressed={modoSelecao} onClick={() => setModoSelecao((v) => !v)}
                  aria-label="Modo de seleção por retângulo" title="Modo de seleção: arrastar o fundo seleciona vários blocos"><Icon name="pick" /></button>
              </div>
            </Panel>
            {nodes.length === 0 && (
              <Panel position="top-center" className="vazio-canvas">
                <Icon name="plus" size={26} />
                <p><strong>Comece arrastando um bloco</strong> da biblioteca para cá.</p>
                <p className="campo-ajuda">Dica: um fluxo costuma começar em “Início manual” ou “Valor constante” e terminar em “Saída final”.</p>
              </Panel>
            )}
            <MiniMap pannable zoomable position="bottom-left" ariaLabel="Minimapa da área de trabalho" style={{ width: 150, height: 96 }}
              nodeColor={(n) => corDaCategoria((n.data as BlockNode['data']).def?.category ?? 'Personalizados')} maskColor="rgba(30,36,48,0.12)" />
          </ReactFlow>
        </main>
        {mostrarConfig && (
          <ConfigPanel
            selecionados={selecionados} todos={nodes} arestas={edges} problemas={analise.issues} sistema={sistema} acoes={acoes}
            nomeProjeto={nome} descricaoProjeto={descricao} onNomeProjeto={setNome} onDescricaoProjeto={setDescricao}
          />
        )}
      </div>

      <ErrorBoundary titulo="Não foi possível exibir os resultados" resetKey={run?.id}
        acao={<button className="btn btn-pequeno" onClick={() => setRun(null)}>Limpar resultado</button>}>
      <BottomPanel
        aba={aba} onAba={setAba} run={run} executando={executando} nomes={nomes} problemas={analise.issues}
        historico={historico} onAbrirExecucao={(id) => void abrirExecucao(id)} onIrParaBloco={irParaBloco}
        aberto={painelAberto} onAlternar={() => setPainelAberto((v) => !v)} altura={altura} onAltura={setAltura}
        onExecutar={() => void executar()} onLimpar={() => setRun(null)}
      />
      </ErrorBoundary>

      {dialogo?.tipo === 'testar' && noTeste?.data.def && (
        <TestarBlocoDialog no={noTeste} projectId={projeto.id} onClose={() => setDialogo(null)} />
      )}
      {dialogo?.tipo === 'executar-dados' && (
        <ExecutarComDadosDialog inicios={inicios} onClose={() => setDialogo(null)}
          onExecutar={(d) => { setDialogo(null); void executar(d) }} />
      )}
      {dialogo?.tipo === 'atalhos' && <AtalhosDialog onClose={() => setDialogo(null)} />}
      {dialogo?.tipo === 'bloco' && (
        <BlockEditorDialog
          editar={dialogo.editar} baseVersao={dialogo.baseVersao} categorias={[...new Set(biblioteca.map((b) => b.category))]}
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
          <p>Há alterações que ainda não foram salvas neste projeto.</p>
        </Dialog>
      )}
      {dialogo?.tipo === 'conflito' && (
        <Confirmar titulo="Este projeto mudou em outra aba" rotuloConfirmar="Exportar meu fluxo" perigo={false}
          mensagem="Outra aba ou janela salvou este projeto depois que você o abriu. Para não perder o seu trabalho, exporte o fluxo atual e depois recarregue a página para ver a versão salva."
          onConfirmar={() => { void exportar(); setDialogo(null) }} onCancelar={() => setDialogo(null)} />
      )}
    </div>
  )
}
