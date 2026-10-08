import { useMemo, useRef, useState } from 'react'
import { api, ApiFailure } from '../api'
import { valorPadraoDoTipo } from '../lib/flow'
import { ROTULO_TIPO_LONGO, slugDeId } from '../lib/visual'
import type { BlockDraft, BlockType, Issue, ParamDef, PortDef, Run, TipoDado, TipoParametro } from '../types'
import { CodeEditor } from './CodeEditor'
import { Icon } from './Icons'
import { ResultadoDoTeste } from './TestResult'
import { Aviso, Confirmar, Dialog } from './ui'
import { ValueField } from './ValueField'

const RE_ID = /^[a-z_][a-z0-9_]{0,39}$/
const TIPOS: TipoDado[] = ['texto', 'numero', 'booleano', 'lista', 'json', 'qualquer']
const TIPOS_PARAM: TipoParametro[] = ['texto', 'numero', 'booleano', 'lista', 'json']

interface LinhaPorta { id: string; label: string; type: TipoDado; required: boolean; description: string; idAuto: boolean }
// `original` guarda a definição completa (opções de seleção, faixa, visibilidade…) para não perder o que o editor não edita.
interface LinhaParam { id: string; label: string; type: TipoParametro; required: boolean; default: unknown; help: string; idAuto: boolean; original?: ParamDef }

const CODIGO_INICIAL = `def run(inputs: dict, params: dict) -> dict:
    # inputs: os dados que chegam pelas conexões, pelo nome de cada entrada.
    # params: a configuração definida pelo usuário no painel do bloco.
    # Devolva um dicionário com as saídas declaradas ao lado.
    nome = inputs.get("nome", "mundo")
    return {"mensagem": f"Olá, {nome}!"}
`

function gerarModelo(entradas: LinhaPorta[], saidas: LinhaPorta[], params: LinhaParam[]): string {
  const linhas = ['def run(inputs: dict, params: dict) -> dict:']
  for (const e of entradas) {
    linhas.push(e.required
      ? `    ${e.id} = inputs["${e.id}"]  # ${e.label} (${e.type})`
      : `    ${e.id} = inputs.get("${e.id}")  # ${e.label} (${e.type}, opcional)`)
  }
  for (const p of params) linhas.push(`    ${p.id} = params.get("${p.id}")  # parâmetro: ${p.label} (${p.type})`)
  if (entradas.length || params.length) linhas.push('')
  linhas.push('    # escreva sua lógica aqui', '    return {')
  for (const s of saidas) linhas.push(`        "${s.id}": None,  # ${s.label} (${s.type})`)
  linhas.push('    }', '')
  return linhas.join('\n')
}

function dePorta(p: PortDef): LinhaPorta {
  return { id: p.id, label: p.label, type: p.type, required: p.required, description: p.description, idAuto: false }
}
function deParam(p: ParamDef): LinhaParam {
  return { id: p.id, label: p.label, type: p.type, required: p.required, default: p.default, help: p.help, idAuto: false, original: p }
}

const PARAM_PADRAO = { options: [], placeholder: '', multiline: false, allow_empty: false, min: null, max: null, type_from: null, visible_when: null } as const

function problemasDeIds(linhas: { id: string }[], rotulo: string): string[] {
  const out: string[] = []
  const vistos = new Set<string>()
  linhas.forEach((l, i) => {
    if (!RE_ID.test(l.id)) out.push(`${rotulo} ${i + 1}: o identificador “${l.id || '(vazio)'}” é inválido. Use letras minúsculas, números e _, começando por letra.`)
    else if (vistos.has(l.id)) out.push(`${rotulo} ${i + 1}: o identificador “${l.id}” está repetido.`)
    vistos.add(l.id)
  })
  return out
}

export function BlockEditorDialog({
  editar, baseVersao, categorias, executorOk, onClose, onSalvo, onExcluido,
}: {
  editar?: BlockType
  baseVersao?: number
  categorias: string[]
  executorOk: boolean
  onClose: () => void
  onSalvo: (b: BlockType, avisos: string[]) => void
  onExcluido?: () => void
}) {
  const [nome, setNome] = useState(editar?.name ?? '')
  const [descricao, setDescricao] = useState(editar?.description ?? '')
  const [categoria, setCategoria] = useState(editar?.category ?? 'Personalizados')
  const [entradas, setEntradas] = useState<LinhaPorta[]>(
    editar ? editar.inputs.map(dePorta) : [{ id: 'nome', label: 'Nome', type: 'texto', required: false, description: '', idAuto: false }])
  const [saidas, setSaidas] = useState<LinhaPorta[]>(
    editar ? editar.outputs.map(dePorta) : [{ id: 'mensagem', label: 'Mensagem', type: 'texto', required: true, description: '', idAuto: false }])
  const [params, setParams] = useState<LinhaParam[]>(editar ? editar.params.map(deParam) : [])
  const [codigo, setCodigo] = useState(editar?.code ?? CODIGO_INICIAL)

  const [valoresTeste, setValoresTeste] = useState<Record<string, unknown>>({})
  const [usarTeste, setUsarTeste] = useState<Record<string, boolean>>({})
  const [paramsTeste, setParamsTeste] = useState<Record<string, unknown>>({})
  const [run, setRun] = useState<Run | null>(null)
  const [testando, setTestando] = useState(false)
  const [salvando, setSalvando] = useState(false)
  const [falha, setFalha] = useState<{ mensagem: string; issues: Issue[]; sugestao?: string | null } | null>(null)
  const [linhaErro, setLinhaErro] = useState<number | null>(null)
  const [confirmarExcluir, setConfirmarExcluir] = useState(false)
  const [confirmarDescartar, setConfirmarDescartar] = useState(false)

  const problemasLocais = useMemo(() => {
    const p: string[] = []
    if (!nome.trim()) p.push('Dê um nome ao bloco.')
    if (saidas.length === 0) p.push('Declare ao menos uma saída.')
    p.push(...problemasDeIds(entradas, 'Entrada'), ...problemasDeIds(saidas, 'Saída'), ...problemasDeIds(params, 'Parâmetro'))
    for (const [lista, rotulo] of [[entradas, 'Entrada'], [saidas, 'Saída'], [params, 'Parâmetro']] as const) {
      lista.forEach((l, i) => { if (!l.label.trim()) p.push(`${rotulo} ${i + 1}: informe o nome visível.`) })
    }
    return p
  }, [nome, entradas, saidas, params])

  const instantaneo = JSON.stringify([nome, descricao, categoria, entradas, saidas, params, codigo])
  const inicial = useRef(instantaneo)
  const alterado = instantaneo !== inicial.current

  /** Fechar sem salvar (Esc, X, Cancelar) pede confirmação se houver alterações: nome, portas e código não se perdem sem aviso. */
  function fechar() {
    if (alterado) setConfirmarDescartar(true)
    else onClose()
  }

  function rascunho(): BlockDraft {
    return {
      name: nome.trim(), description: descricao.trim(), category: categoria.trim() || 'Personalizados', code: codigo,
      inputs: entradas.map((e) => ({ id: e.id, label: e.label.trim(), type: e.type, required: e.required, description: e.description, conditional: false })),
      outputs: saidas.map((s) => ({ id: s.id, label: s.label.trim(), type: s.type, required: true, description: s.description, conditional: false })),
      params: params.map((p) => {
        const base = p.original ?? PARAM_PADRAO
        return {
          ...base, id: p.id, label: p.label.trim(), type: p.type, required: p.required, default: p.default ?? null, help: p.help,
          options: p.type === 'selecao' ? [...base.options] : [],
        }
      }),
    }
  }

  async function testar() {
    setTestando(true); setFalha(null); setRun(null); setLinhaErro(null)
    try {
      const inputs = Object.fromEntries(entradas.filter((e) => e.required || usarTeste[e.id])
        .map((e) => [e.id, e.id in valoresTeste ? valoresTeste[e.id] : valorPadraoDoTipo(e.type === 'qualquer' ? 'texto' : e.type)]))
      const r = await api.testarBloco({ draft: rascunho(), params: paramsTeste, inputs })
      setRun(r)
      setLinhaErro(r.steps[0]?.error?.technical?.line ?? null)
    } catch (e) {
      const f = e as ApiFailure
      setFalha({ mensagem: f.message, issues: f.issues, sugestao: f.suggestion })
    } finally {
      setTestando(false)
    }
  }

  async function salvar() {
    setSalvando(true); setFalha(null); setLinhaErro(null)
    try {
      const r = editar ? await api.novaVersaoDoBloco(editar.id, rascunho()) : await api.criarBloco(rascunho())
      onSalvo(r.block, r.warnings)
    } catch (e) {
      const f = e as ApiFailure
      setFalha({ mensagem: f.message, issues: f.issues, sugestao: f.suggestion })
      const linha = (f.issues[0] as unknown as { technical?: { line?: number } } | undefined)?.technical?.line
      if (linha) setLinhaErro(linha)
    } finally {
      setSalvando(false)
    }
  }

  async function excluir() {
    try {
      await api.excluirBloco(editar!.id)
      onExcluido?.()
      onClose()
    } catch (e) {
      const f = e as ApiFailure
      setConfirmarExcluir(false)
      setFalha({ mensagem: f.message, issues: [], sugestao: f.suggestion })
    }
  }

  function atualizarLinha<T extends { id: string; label: string; idAuto: boolean }>(
    lista: T[], set: (l: T[]) => void, i: number, mudanca: Partial<T>,
  ) {
    set(lista.map((l, k) => {
      if (k !== i) return l
      const nova = { ...l, ...mudanca }
      if ('label' in mudanca && l.idAuto) nova.id = slugDeId(nova.label)
      if ('id' in mudanca) nova.idAuto = false
      return nova
    }))
  }

  const titulo = editar ? `Editar bloco “${editar.name}”` : 'Novo bloco Python'
  const proxima = editar ? 'uma nova versão' : 'a v1'

  return (
    <Dialog titulo={titulo} onClose={fechar} tela fecharAoClicarFora={false}
      descricao={editar
        ? `Você está editando a v${baseVersao ?? editar.version}. Ao salvar, será criada ${proxima} na biblioteca; fluxos que já usam outra versão continuam nela, sem mudar.`
        : 'Declare as entradas, saídas e parâmetros, escreva a função run e teste com dados de exemplo antes de salvar na biblioteca.'}
      rodape={<>
        {editar && <button className="btn btn-perigo-suave rodape-esquerda" onClick={() => setConfirmarExcluir(true)}><Icon name="trash" size={16} /> Excluir bloco</button>}
        <button className="btn" onClick={fechar}>Cancelar</button>
        <button className="btn btn-primario" onClick={salvar} disabled={salvando || problemasLocais.length > 0}
          title={problemasLocais[0]}>
          <Icon name="save" size={16} /> {salvando ? 'Salvando…' : editar ? 'Salvar nova versão' : 'Salvar na biblioteca'}
        </button>
      </>}>
      {!executorOk && (
        <Aviso tipo="aviso" titulo="Executor isolado indisponível">
          Você pode escrever e salvar o bloco, mas o código só poderá ser verificado, testado e executado quando o Docker estiver disponível.
        </Aviso>
      )}
      {falha && (
        <Aviso tipo="erro" titulo={falha.mensagem}>
          {falha.issues.map((i, k) => <p key={k}>{i.message}</p>)}
          {falha.sugestao && <p>{falha.sugestao}</p>}
        </Aviso>
      )}
      <div className="editor-bloco">
        <div className="editor-bloco-form">
          <h3 className="secao">Identificação</h3>
          <div className="campo">
            <label htmlFor="eb-nome">Nome do bloco <span className="obrigatorio">*</span></label>
            <input id="eb-nome" value={nome} maxLength={60} onChange={(e) => setNome(e.target.value)} data-autofocus />
          </div>
          <div className="campo">
            <label htmlFor="eb-desc">O que ele faz</label>
            <textarea id="eb-desc" rows={2} maxLength={500} value={descricao} onChange={(e) => setDescricao(e.target.value)} />
          </div>
          <div className="campo">
            <label htmlFor="eb-cat">Categoria</label>
            <input id="eb-cat" list="eb-categorias" value={categoria} maxLength={40} onChange={(e) => setCategoria(e.target.value)} />
            <datalist id="eb-categorias">{[...new Set(['Personalizados', ...categorias])].map((c) => <option key={c} value={c} />)}</datalist>
          </div>

          <TabelaPortas titulo="Entradas" rotulo="entrada" linhas={entradas} comObrigatoria
            ajuda="O que o bloco recebe pelas conexões. No código: inputs[“identificador”]."
            onChange={(i, m) => atualizarLinha(entradas, setEntradas, i, m)}
            onAdicionar={() => setEntradas([...entradas, { id: '', label: '', type: 'texto', required: true, description: '', idAuto: true }])}
            onRemover={(i) => setEntradas(entradas.filter((_, k) => k !== i))} />
          <TabelaPortas titulo="Saídas" rotulo="saída" linhas={saidas}
            ajuda="O que o bloco devolve. As chaves do dicionário retornado por run() precisam ser exatamente estas."
            onChange={(i, m) => atualizarLinha(saidas, setSaidas, i, m)}
            onAdicionar={() => setSaidas([...saidas, { id: '', label: '', type: 'texto', required: true, description: '', idAuto: true }])}
            onRemover={(i) => setSaidas(saidas.filter((_, k) => k !== i))} />

          <section aria-label="Parâmetros">
            <h3 className="secao">Parâmetros</h3>
            <p className="campo-ajuda">Configurações que quem usa o bloco ajusta no painel da direita. No código: params[“identificador”].</p>
            {params.map((p, i) => (
              <fieldset className="linha-declaracao" key={i}>
                <legend>Parâmetro {i + 1}</legend>
                <div className="grade-2">
                  <div className="campo"><label htmlFor={`pl-${i}`}>Nome visível</label>
                    <input id={`pl-${i}`} value={p.label} maxLength={60} onChange={(e) => atualizarLinha(params, setParams, i, { label: e.target.value })} /></div>
                  <div className="campo"><label htmlFor={`pi-${i}`}>Identificador</label>
                    <input id={`pi-${i}`} className="mono" value={p.id} maxLength={40} onChange={(e) => atualizarLinha(params, setParams, i, { id: e.target.value })}
                      aria-invalid={!RE_ID.test(p.id)} /></div>
                </div>
                <div className="grade-2">
                  <div className="campo"><label htmlFor={`pt-${i}`}>Tipo</label>
                    <select id={`pt-${i}`} value={p.type} onChange={(e) => atualizarLinha(params, setParams, i, { type: e.target.value as TipoParametro, default: null })}>
                      {TIPOS_PARAM.map((t) => <option key={t} value={t}>{ROTULO_TIPO_LONGO[t as TipoDado]}</option>)}
                      {p.original?.type === 'selecao' && <option value="selecao">Seleção (opções fixas)</option>}
                      {p.original?.type === 'codigo' && <option value="codigo">Código</option>}
                    </select></div>
                  <div className="campo">
                    <ValueField tipo={p.type} rotulo="Valor padrão" valor={p.default ?? valorPadraoDoTipo(p.type)} key={`${i}-${p.type}`}
                      opcoes={p.original?.options}
                      onChange={(v) => atualizarLinha(params, setParams, i, { default: v })} />
                  </div>
                </div>
                <div className="campo"><label htmlFor={`ph-${i}`}>Texto de ajuda</label>
                  <input id={`ph-${i}`} value={p.help} maxLength={400} onChange={(e) => atualizarLinha(params, setParams, i, { help: e.target.value })} /></div>
                <div className="acoes-linha">
                  <label className="checagem"><input type="checkbox" checked={p.required} onChange={(e) => atualizarLinha(params, setParams, i, { required: e.target.checked })} /> Obrigatório</label>
                  <button className="btn btn-pequeno btn-perigo-suave" onClick={() => setParams(params.filter((_, k) => k !== i))}><Icon name="trash" size={14} /> Remover parâmetro {i + 1}</button>
                </div>
              </fieldset>
            ))}
            <button className="btn btn-pequeno" onClick={() => setParams([...params, { id: '', label: '', type: 'texto', required: false, default: '', help: '', idAuto: true }])}>
              <Icon name="plus" size={14} /> Adicionar parâmetro
            </button>
          </section>
        </div>

        <div className="editor-bloco-codigo">
          <div className="titulo-com-acao">
            <h3 className="secao">Código Python</h3>
            <button className="btn btn-pequeno" onClick={() => { if (codigo === CODIGO_INICIAL || confirm('Substituir o código atual por um modelo gerado a partir das entradas e saídas?')) setCodigo(gerarModelo(entradas, saidas, params)) }}>
              Gerar modelo do código
            </button>
          </div>
          <CodeEditor value={codigo} onChange={(v) => { setCodigo(v); setLinhaErro(null) }} ariaLabel="Código Python do bloco" linhaComErro={linhaErro} altura={300} />
          <p className="campo-ajuda">
            A função <code>run(inputs, params)</code> deve devolver um dicionário com as saídas declaradas, em formato JSON (texto, número, sim/não, lista ou objeto).
            Bibliotecas permitidas: apenas a biblioteca padrão do Python (math, json, re, datetime, statistics…). Sem acesso à internet nem a arquivos.
            Para sair do editor com o teclado: Esc, depois Tab.
          </p>

          <h3 className="secao">Testar com dados de exemplo</h3>
          {entradas.map((e) => (
            <div key={e.id || Math.random()} className="campo-teste">
              {!e.required && (
                <label className="checagem">
                  <input type="checkbox" checked={!!usarTeste[e.id]} onChange={(ev) => setUsarTeste((u) => ({ ...u, [e.id]: ev.target.checked }))} />
                  Informar “{e.label || e.id}” (opcional)
                </label>
              )}
              {(e.required || usarTeste[e.id]) && e.id && (
                <ValueField tipo={e.type} rotulo={`Entrada “${e.label || e.id}”`} obrigatorio={e.required}
                  valor={e.id in valoresTeste ? valoresTeste[e.id] : valorPadraoDoTipo(e.type === 'qualquer' ? 'texto' : e.type)}
                  onChange={(v) => setValoresTeste((t) => ({ ...t, [e.id]: v }))} />
              )}
            </div>
          ))}
          {params.filter((p) => p.id).map((p) => (
            <ValueField key={`${p.id}-${p.type}`} tipo={p.type} rotulo={`Parâmetro “${p.label || p.id}”`}
              valor={p.id in paramsTeste ? paramsTeste[p.id] : p.default ?? valorPadraoDoTipo(p.type)}
              onChange={(v) => setParamsTeste((t) => ({ ...t, [p.id]: v }))} />
          ))}
          <button className="btn btn-primario-suave" onClick={testar} disabled={testando || !executorOk || problemasLocais.length > 0}
            title={!executorOk ? 'O executor isolado está indisponível' : problemasLocais[0]}>
            <Icon name="play" size={16} /> {testando ? 'Testando…' : 'Testar bloco'}
          </button>
          {problemasLocais.length > 0 && (
            <ul className="problemas-locais" aria-label="O que falta para salvar">
              {problemasLocais.map((p, i) => <li key={i}><Icon name="alert" size={14} /> {p}</li>)}
            </ul>
          )}
          {run && <ResultadoDoTeste run={run} />}
        </div>
      </div>
      {confirmarDescartar && (
        <Confirmar titulo="Descartar as alterações?" rotuloConfirmar="Descartar" perigo
          mensagem="Você mudou este bloco e ainda não salvou. Se fechar agora, o que foi digitado (nome, portas e código) será perdido."
          onConfirmar={onClose} onCancelar={() => setConfirmarDescartar(false)} />
      )}
      {confirmarExcluir && (
        <Confirmar titulo="Excluir este bloco?" rotuloConfirmar="Excluir bloco" perigo
          mensagem="O bloco e todas as suas versões serão removidos da biblioteca. Se algum projeto ainda o usa, a exclusão é recusada."
          onConfirmar={() => void excluir()} onCancelar={() => setConfirmarExcluir(false)} />
      )}
    </Dialog>
  )
}

function TabelaPortas({
  titulo, rotulo, linhas, comObrigatoria, ajuda, onChange, onAdicionar, onRemover,
}: {
  titulo: string; rotulo: string; linhas: LinhaPorta[]; comObrigatoria?: boolean; ajuda: string
  onChange: (i: number, m: Partial<LinhaPorta>) => void; onAdicionar: () => void; onRemover: (i: number) => void
}) {
  return (
    <section aria-label={titulo}>
      <h3 className="secao">{titulo}</h3>
      <p className="campo-ajuda">{ajuda}</p>
      {linhas.map((l, i) => (
        <fieldset className="linha-declaracao" key={i}>
          <legend>{titulo.slice(0, -1)} {i + 1}</legend>
          <div className="grade-3">
            <div className="campo"><label htmlFor={`${rotulo}-l-${i}`}>Nome visível</label>
              <input id={`${rotulo}-l-${i}`} value={l.label} maxLength={60} onChange={(e) => onChange(i, { label: e.target.value })} /></div>
            <div className="campo"><label htmlFor={`${rotulo}-i-${i}`}>Identificador</label>
              <input id={`${rotulo}-i-${i}`} className="mono" value={l.id} maxLength={40} aria-invalid={!RE_ID.test(l.id)}
                onChange={(e) => onChange(i, { id: e.target.value })} /></div>
            <div className="campo"><label htmlFor={`${rotulo}-t-${i}`}>Tipo</label>
              <select id={`${rotulo}-t-${i}`} value={l.type} onChange={(e) => onChange(i, { type: e.target.value as TipoDado })}>
                {TIPOS.map((t) => <option key={t} value={t}>{ROTULO_TIPO_LONGO[t]}</option>)}
              </select></div>
          </div>
          <div className="acoes-linha">
            {comObrigatoria && (
              <label className="checagem"><input type="checkbox" checked={l.required} onChange={(e) => onChange(i, { required: e.target.checked })} />
                Obrigatória (precisa estar conectada)</label>
            )}
            <button className="btn btn-pequeno btn-perigo-suave" onClick={() => onRemover(i)}><Icon name="trash" size={14} /> Remover {rotulo} {i + 1}</button>
          </div>
        </fieldset>
      ))}
      <button className="btn btn-pequeno" onClick={onAdicionar}><Icon name="plus" size={14} /> Adicionar {rotulo}</button>
    </section>
  )
}
