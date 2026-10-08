import { forwardRef, useEffect, useId, useImperativeHandle, useMemo, useRef, useState } from 'react'
import { campoDePartes, ehDinamico, partesDe, referenciaUnica, referenciasDe, tipoCompativel, type GrupoDinamico } from '../lib/modelo'
import { iconeDoBloco, ROTULO_TIPO } from '../lib/visual'
import type { Campo, Ref, TipoDado } from '../types'
import { Icon } from './Icons'
import { ValueField } from './ValueField'

/** O que o campo precisa saber sobre os passos anteriores. */
export interface FonteDinamica {
  grupos: GrupoDinamico[]
  /** Como mostrar uma referência: rótulo da saída, nome do passo e se ainda é válida. */
  descrever: (ref: Ref) => { saida: string; passo: string; valido: boolean }
}

type Parte = string | Ref

function normalizar(partes: Parte[]): Parte[] {
  const out: Parte[] = []
  for (const p of partes) {
    if (typeof p === 'string') {
      if (p === '') continue
      if (typeof out[out.length - 1] === 'string') out[out.length - 1] = (out[out.length - 1] as string) + p
      else out.push(p)
    } else out.push(p)
  }
  return out
}

const rotuloDaRef = (fonte: FonteDinamica, r: Ref) => {
  const d = fonte.descrever(r)
  return `${d.valido ? d.saida : 'conteúdo removido'}${r.path ? `.${r.path}` : ''}`
}

// ------------------------------------------------------------------ editor de texto com “chips”
export interface TextoComTokensRef {
  inserir: (ref: Ref) => void
  focar: () => void
}

interface TextoProps {
  rotuloId: string
  partes: Parte[]
  onPartes: (p: Parte[]) => void
  fonte: FonteDinamica
  multilinha?: boolean
  placeholder?: string
  desabilitado?: boolean
  invalido?: boolean
  obrigatorio?: boolean
  descricaoId?: string
  rotuloTexto: string
}

function criarChip(r: Ref, fonte: FonteDinamica): HTMLElement {
  const d = fonte.descrever(r)
  const chip = document.createElement('span')
  chip.className = `chip-dinamico${d.valido ? '' : ' chip-invalido'}`
  chip.contentEditable = 'false'
  chip.dataset.ref = JSON.stringify(r)
  chip.setAttribute('role', 'img')
  chip.setAttribute('aria-label', d.valido ? `Conteúdo dinâmico: ${d.saida}, do passo ${d.passo}` : 'Conteúdo dinâmico removido')
  chip.title = d.valido ? `${d.saida} — ${d.passo}` : 'O passo de origem não existe mais'
  chip.textContent = rotuloDaRef(fonte, r)
  return chip
}

export const TextoComTokens = forwardRef<TextoComTokensRef, TextoProps>(function TextoComTokens({
  rotuloId, partes, onPartes, fonte, multilinha, placeholder, desabilitado, invalido, obrigatorio, descricaoId, rotuloTexto,
}, ref) {
  const raiz = useRef<HTMLDivElement>(null)
  const ultimo = useRef('')  // o que o DOM mostra agora (conteúdo + nomes dos chips), para não redesenhar enquanto o usuário digita
  const selecao = useRef<Range | null>(null)
  const emitir = useRef(onPartes)
  emitir.current = onPartes

  function renderizar(ps: Parte[]) {
    const el = raiz.current
    if (!el) return
    el.replaceChildren()
    for (const p of ps) el.appendChild(typeof p === 'string' ? document.createTextNode(p) : criarChip(p, fonte))
    if (ps.length && typeof ps[ps.length - 1] === 'string' && (ps[ps.length - 1] as string).endsWith('\n')) {
      const br = document.createElement('br')
      br.dataset.s = '1'
      el.appendChild(br)
    }
  }

  function ler(): Parte[] {
    const out: Parte[] = []
    const visitar = (no: Node) => {
      no.childNodes.forEach((c) => {
        if (c.nodeType === Node.TEXT_NODE) out.push(c.textContent ?? '')
        else if (c instanceof HTMLElement) {
          if (c.dataset.ref) {
            try { out.push(JSON.parse(c.dataset.ref) as Ref) } catch { /* chip adulterado: ignora */ }
          } else if (c.tagName === 'BR') { if (!c.dataset.s) out.push('\n') }
          else { if (out.length) out.push('\n'); visitar(c) }  // quebras que o navegador cria (DIV/P)
        }
      })
    }
    visitar(raiz.current!)
    return normalizar(out)
  }

  const assinatura = (ps: Parte[]) => JSON.stringify(ps.map((p) => (typeof p === 'string' ? p : [p, rotuloDaRef(fonte, p), fonte.descrever(p).valido])))

  function publicar() {
    let novas = ler()
    if (!multilinha) novas = normalizar(novas.map((p) => (typeof p === 'string' ? p.replace(/\n/g, ' ') : p)))
    ultimo.current = assinatura(novas)
    emitir.current(novas)
  }

  // Sincroniza o DOM com o valor de fora, sem mexer no cursor enquanto o usuário digita.
  useEffect(() => {
    const norm = normalizar(partes)
    const chave = assinatura(norm)
    if (chave === ultimo.current) return
    renderizar(norm)
    ultimo.current = chave
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [partes, fonte])

  // Guarda a posição do cursor para inserir o conteúdo dinâmico escolhido no lugar certo.
  useEffect(() => {
    const guardar = () => {
      const s = window.getSelection()
      if (s && s.rangeCount && raiz.current?.contains(s.anchorNode)) selecao.current = s.getRangeAt(0).cloneRange()
    }
    document.addEventListener('selectionchange', guardar)
    return () => document.removeEventListener('selectionchange', guardar)
  }, [])

  useImperativeHandle(ref, () => ({
    focar: () => raiz.current?.focus(),
    inserir: (r: Ref) => {
      const el = raiz.current
      if (!el) return
      el.focus()
      const s = window.getSelection()!
      let range = selecao.current && el.contains(selecao.current.startContainer) ? selecao.current : null
      if (!range) { range = document.createRange(); range.selectNodeContents(el); range.collapse(false) }
      range.deleteContents()
      const chip = criarChip(r, fonte)
      range.insertNode(chip)
      range.setStartAfter(chip)
      range.collapse(true)
      s.removeAllRanges()
      s.addRange(range)
      selecao.current = range.cloneRange()
      publicar()
    },
  }))

  function inserirTexto(texto: string) {
    const s = window.getSelection()
    if (!s || !s.rangeCount) return
    const r = s.getRangeAt(0)
    r.deleteContents()
    const t = document.createTextNode(texto)
    r.insertNode(t)
    r.setStartAfter(t)
    r.collapse(true)
    s.removeAllRanges()
    s.addRange(r)
    publicar()
  }

  return (
    <div
      ref={raiz} id={rotuloId + '-campo'} role="textbox" aria-multiline={multilinha ? true : undefined} aria-labelledby={rotuloId}
      aria-describedby={descricaoId} aria-invalid={invalido ? true : undefined} aria-required={obrigatorio ? true : undefined}
      aria-disabled={desabilitado ? true : undefined} data-rotulo={rotuloTexto}
      contentEditable={!desabilitado} suppressContentEditableWarning spellCheck={false}
      className={`texto-dinamico${multilinha ? ' multilinha' : ''}`} data-placeholder={placeholder ?? ''} tabIndex={desabilitado ? -1 : 0}
      onInput={publicar}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault()
          if (multilinha) inserirTexto('\n')
        }
      }}
      onPaste={(e) => {
        e.preventDefault()
        const texto = e.clipboardData.getData('text/plain')
        inserirTexto(multilinha ? texto : texto.replace(/\s*\n\s*/g, ' '))
      }}
      onDrop={(e) => e.preventDefault()}
    />
  )
})

// ------------------------------------------------------------------ seletor de conteúdo dinâmico
export function SeletorDeConteudo({
  fonte, tipoEsperado, aceitaQualquerTipo, onEscolher, onFechar,
}: {
  fonte: FonteDinamica
  tipoEsperado: TipoDado
  aceitaQualquerTipo: boolean
  onEscolher: (r: Ref) => void
  onFechar: () => void
}) {
  const [busca, setBusca] = useState('')
  const q = busca.trim().toLowerCase()
  const grupos = useMemo(() => fonte.grupos
    .map((g) => ({ ...g, saidas: g.saidas.filter((s) => !q || `${s.rotulo} ${g.nome}`.toLowerCase().includes(q)) }))
    .filter((g) => g.saidas.length), [fonte.grupos, q])

  return (
    <div className="seletor-dinamico" role="group" aria-label="Conteúdo dinâmico disponível"
      onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); onFechar() } }}>
      <div className="seletor-topo">
        <strong>Conteúdo dinâmico</strong>
        <button type="button" className="btn-icone" aria-label="Fechar o seletor de conteúdo dinâmico" onClick={onFechar}><Icon name="x" size={16} /></button>
      </div>
      <div className="busca">
        <Icon name="search" size={16} />
        <input type="search" aria-label="Buscar conteúdo dinâmico" placeholder="Buscar…" value={busca} onChange={(e) => setBusca(e.target.value)} data-autofocus />
      </div>
      <div className="seletor-lista">
        {grupos.length === 0 && (
          <p className="campo-ajuda">{fonte.grupos.length === 0 ? 'Ainda não há conteúdo de passos anteriores para usar aqui.' : `Nada encontrado para “${busca}”.`}</p>
        )}
        {grupos.map((g) => (
          <section key={`${g.passoId}-${g.dentro}`} aria-label={g.nome} className="seletor-grupo">
            <h4><Icon name={iconeDoBloco(g.tipoId)} size={14} /> {g.nome}{g.dentro && <span className="seletor-dentro"> · só dentro do bloco</span>}</h4>
            <ul>
              {g.saidas.map((s) => {
                const ok = aceitaQualquerTipo || tipoCompativel(s.tipo, tipoEsperado)
                return (
                  <li key={s.ref.output}>
                    <button type="button" className="seletor-item" disabled={!ok} onClick={() => onEscolher(s.ref)}
                      title={ok ? s.descricao || undefined : `Este campo espera ${ROTULO_TIPO[tipoEsperado]}, mas este conteúdo é ${ROTULO_TIPO[s.tipo]}.`}>
                      <span className="chip-dinamico">{s.rotulo}</span>
                      <span className="seletor-tipo">{ROTULO_TIPO[s.tipo]}{!ok ? ' — tipo incompatível' : ''}</span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </section>
        ))}
      </div>
      <p className="seletor-dica">
        <Icon name="python" size={14} />
        <span>Precisa de uma lógica para transformar esses dados? Adicione um passo <strong>Executar código Python</strong>.</span>
      </p>
    </div>
  )
}

// ------------------------------------------------------------------ edição de uma referência (campo interno / remover)
function EditorDeReferencia({
  fonte, referencia, aceitaCaminho, onAplicar, onRemover, onFechar,
}: {
  fonte: FonteDinamica; referencia: Ref; aceitaCaminho: boolean
  onAplicar: (path: string) => void; onRemover: () => void; onFechar: () => void
}) {
  const [path, setPath] = useState(referencia.path)
  const id = useId()
  const d = fonte.descrever(referencia)
  return (
    <div className="editor-referencia" role="group" aria-label={`Conteúdo dinâmico ${d.saida}`}
      onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); onFechar() } }}>
      <p><strong>{d.valido ? d.saida : 'Conteúdo removido'}</strong>{d.valido && <span className="campo-ajuda"> — do passo “{d.passo}”</span>}</p>
      {aceitaCaminho && d.valido && (
        <div className="campo">
          <label htmlFor={id}>Campo interno (opcional)</label>
          <input id={id} type="text" value={path} placeholder="endereco.cidade ou itens.0" onChange={(e) => setPath(e.target.value)} data-autofocus
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); onAplicar(path.trim()) } }} />
          <p className="campo-ajuda">Para pegar só uma parte de um objeto ou lista. Use ponto para campos internos e números para posições.</p>
        </div>
      )}
      <div className="acoes-linha">
        {aceitaCaminho && d.valido && <button type="button" className="btn btn-pequeno btn-primario-suave" onClick={() => onAplicar(path.trim())}>Aplicar</button>}
        <button type="button" className="btn btn-pequeno btn-perigo-suave" onClick={onRemover}><Icon name="trash" size={14} /> Remover do campo</button>
        <button type="button" className="btn btn-pequeno" onClick={onFechar}>Fechar</button>
      </div>
    </div>
  )
}

// ------------------------------------------------------------------ o campo completo
const TIPOS_LITERAIS: { value: Exclude<TipoDado, 'qualquer'>; label: string }[] = [
  { value: 'texto', label: 'Texto' }, { value: 'numero', label: 'Número' }, { value: 'booleano', label: 'Sim/Não' },
  { value: 'lista', label: 'Lista' }, { value: 'json', label: 'Objeto JSON' },
]

function tipoDoValor(v: unknown): Exclude<TipoDado, 'qualquer'> {
  if (typeof v === 'number') return 'numero'
  if (typeof v === 'boolean') return 'booleano'
  if (Array.isArray(v)) return 'lista'
  if (v && typeof v === 'object') return 'json'
  return 'texto'
}

const padraoDoLiteral = (t: string): unknown => (t === 'numero' ? 0 : t === 'booleano' ? false : t === 'lista' ? [] : t === 'json' ? {} : '')

export function CampoDinamicoField({
  rotulo, tipo, campo, onChange, fonte, obrigatorio, ajuda, erro, multilinha, placeholder, desabilitado, aceitaDinamico = true,
}: {
  rotulo: string
  tipo: TipoDado
  campo: Campo | undefined
  onChange: (c: Campo | undefined) => void
  fonte: FonteDinamica
  obrigatorio?: boolean
  ajuda?: string
  erro?: string | null
  multilinha?: boolean
  placeholder?: string
  desabilitado?: boolean
  aceitaDinamico?: boolean
}) {
  const id = useId()
  const idRotulo = `${id}-rotulo`
  const idAjuda = `${id}-ajuda`
  const idErro = `${id}-erro`
  const texto = useRef<TextoComTokensRef>(null)
  const [aberto, setAberto] = useState(false)
  const [editando, setEditando] = useState<Ref | null>(null)
  const [tipoEscolhido, setTipoEscolhido] = useState<Exclude<TipoDado, 'qualquer'> | null>(null)

  const unica = referenciaUnica(campo)
  const refs = referenciasDe(campo)
  const literal = campo && !ehDinamico(campo) ? (campo as { value: unknown }).value : undefined
  // Em campos "qualquer" o tipo do valor fixo é escolhido pelo usuário (texto, número, sim/não, lista, objeto).
  const tipoLiteral: Exclude<TipoDado, 'qualquer'> = tipo === 'qualquer' ? (tipoEscolhido ?? tipoDoValor(literal)) : tipo
  const usaTexto = tipo === 'texto' || (tipo === 'qualquer' && (ehDinamico(campo) || tipoLiteral === 'texto'))
  const mensagemErro = erro ?? null
  const descricao = [ajuda ? idAjuda : '', mensagemErro ? idErro : ''].filter(Boolean).join(' ') || undefined

  function aoEscolher(r: Ref) {
    setAberto(false)
    if (usaTexto) { texto.current?.inserir(r); return }
    onChange({ parts: [r] })
  }

  function atualizarRef(antiga: Ref, nova: Ref | null) {
    if (!campo || !ehDinamico(campo)) return
    let usada = false
    const partes = campo.parts.flatMap((p): (string | Ref)[] => {
      if (typeof p === 'string' || usada || JSON.stringify(p) !== JSON.stringify(antiga)) return [p]
      usada = true
      return nova ? [nova] : []
    })
    onChange(campoDePartes(partes))
    setEditando(null)
  }

  return (
    <div className="campo campo-dinamico">
      <div className="rotulo-linha">
        <span id={idRotulo} className="rotulo-campo">{rotulo}{obrigatorio && <span className="obrigatorio" aria-hidden="true"> *</span>}</span>
        {tipo !== 'texto' && <span className="tipo-esperado">{ROTULO_TIPO[tipo]}</span>}
        {tipo === 'qualquer' && !ehDinamico(campo) && (
          <select aria-label={`Tipo do valor de ${rotulo}`} className="seletor-tipo-valor" value={tipoLiteral} disabled={desabilitado}
            onChange={(e) => {
              const novo = e.target.value as Exclude<TipoDado, 'qualquer'>
              setTipoEscolhido(novo)
              onChange({ value: padraoDoLiteral(novo) })
            }}>
            {TIPOS_LITERAIS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        )}
      </div>

      {usaTexto ? (
        <TextoComTokens
          ref={texto} rotuloId={idRotulo} rotuloTexto={rotulo} partes={partesDe(campo)} fonte={fonte}
          onPartes={(p) => {
            const novo = campoDePartes(p)
            // um texto vazio e sem conteúdo dinâmico volta a ser "sem valor" em campos opcionais
            onChange(novo)
          }}
          multilinha={multilinha} placeholder={placeholder} desabilitado={desabilitado} invalido={!!mensagemErro}
          obrigatorio={obrigatorio} descricaoId={descricao}
        />
      ) : unica ? (
        <div className="valor-dinamico">
          <button type="button" className={`chip-dinamico${fonte.descrever(unica).valido ? '' : ' chip-invalido'}`} onClick={() => setEditando(unica)}
            aria-label={`Conteúdo dinâmico ${rotuloDaRef(fonte, unica)}. Abrir opções`}>
            {rotuloDaRef(fonte, unica)}
          </button>
          <button type="button" className="btn btn-pequeno" disabled={desabilitado} onClick={() => { setEditando(null); onChange({ value: padraoDoLiteral(tipoLiteral) }) }}>
            Usar um valor fixo
          </button>
        </div>
      ) : (
        <ValueField
          tipo={tipoLiteral} rotulo={rotulo} valor={literal} onChange={(v) => onChange({ value: v })} obrigatorio={obrigatorio}
          erro={mensagemErro} desabilitado={desabilitado} semRotulo
        />
      )}

      {aceitaDinamico && !desabilitado && (
        <div className="acoes-campo">
          <button type="button" className="btn btn-pequeno btn-dinamico" aria-expanded={aberto} onClick={() => setAberto((v) => !v)}>
            <Icon name="dynamic" size={14} /> Conteúdo dinâmico
          </button>
        </div>
      )}
      {aberto && <SeletorDeConteudo fonte={fonte} tipoEsperado={tipo} aceitaQualquerTipo={usaTexto || tipo === 'qualquer'} onEscolher={aoEscolher} onFechar={() => setAberto(false)} />}

      {usaTexto && refs.length > 0 && (
        <ul className="usando-dinamico" aria-label={`Conteúdos dinâmicos usados em ${rotulo}`}>
          {refs.map((r, i) => (
            <li key={`${i}-${r.step}-${r.output}-${r.path}`}>
              <button type="button" className={`chip-dinamico${fonte.descrever(r).valido ? '' : ' chip-invalido'}`} onClick={() => setEditando(r)}
                aria-label={`Opções do conteúdo dinâmico ${rotuloDaRef(fonte, r)}`}>
                {rotuloDaRef(fonte, r)}
              </button>
            </li>
          ))}
        </ul>
      )}
      {editando && (
        <EditorDeReferencia
          fonte={fonte} referencia={editando} aceitaCaminho
          onAplicar={(path) => (unica && !usaTexto ? onChange({ parts: [{ ...editando, path }] }) : atualizarRef(editando, { ...editando, path }))}
          onRemover={() => (unica && !usaTexto ? (onChange({ value: padraoDoLiteral(tipoLiteral) }), setEditando(null)) : atualizarRef(editando, null))}
          onFechar={() => setEditando(null)}
        />
      )}

      {ajuda && <p id={idAjuda} className="campo-ajuda">{ajuda}</p>}
      {mensagemErro && <p id={idErro} className="campo-erro" role="alert">{mensagemErro}</p>}
    </div>
  )
}
