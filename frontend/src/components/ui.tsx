import {
  createContext, useCallback, useContext, useEffect, useId, useLayoutEffect, useMemo, useRef, useState,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import type { Estado, TipoDado } from '../types'
import { FORMA_TIPO, ICONE_ESTADO, ROTULO_ESTADO, ROTULO_TIPO } from '../lib/visual'
import { Icon } from './Icons'

// ------------------------------------------------------------------ notificações e anúncios
interface Toast {
  id: number
  tipo: 'info' | 'sucesso' | 'erro'
  titulo: string
  detalhe?: string
}

interface Notificador {
  anunciar: (mensagem: string) => void
  sucesso: (titulo: string, detalhe?: string) => void
  info: (titulo: string, detalhe?: string) => void
  erro: (titulo: string, detalhe?: string) => void
}

const Contexto = createContext<Notificador | null>(null)

export function useNotificar(): Notificador {
  const c = useContext(Contexto)
  if (!c) throw new Error('useNotificar fora do NotificacoesProvider')
  return c
}

export function NotificacoesProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const [anuncio, setAnuncio] = useState('')
  const contador = useRef(0)

  const anunciar = useCallback((mensagem: string) => {
    // limpar antes garante que leitores de tela leiam mensagens repetidas
    setAnuncio('')
    window.setTimeout(() => setAnuncio(mensagem), 30)
  }, [])

  const adicionar = useCallback((tipo: Toast['tipo'], titulo: string, detalhe?: string) => {
    const id = ++contador.current
    setToasts((t) => [...t.slice(-3), { id, tipo, titulo, detalhe }])
    if (tipo !== 'erro') window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 6000)
    anunciar(detalhe ? `${titulo}. ${detalhe}` : titulo)
  }, [anunciar])

  const valor = useMemo<Notificador>(() => ({
    anunciar,
    sucesso: (t, d) => adicionar('sucesso', t, d),
    info: (t, d) => adicionar('info', t, d),
    erro: (t, d) => adicionar('erro', t, d),
  }), [adicionar, anunciar])

  return (
    <Contexto.Provider value={valor}>
      {children}
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">{anuncio}</div>
      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.tipo}`}>
            <Icon name={t.tipo === 'erro' ? 'alert' : t.tipo === 'sucesso' ? 'check' : 'info'} />
            <div className="toast-texto">
              <strong>{t.titulo}</strong>
              {t.detalhe && <span>{t.detalhe}</span>}
            </div>
            <button className="btn-icone" aria-label="Dispensar notificação"
              onClick={() => setToasts((l) => l.filter((x) => x.id !== t.id))}><Icon name="x" size={16} /></button>
          </div>
        ))}
      </div>
    </Contexto.Provider>
  )
}

// ------------------------------------------------------------------ diálogo acessível
const FOCAVEIS = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function Dialog({
  titulo, descricao, onClose, children, rodape, largura = 560, tela = false, fecharAoClicarFora = true,
}: {
  titulo: string
  descricao?: string
  onClose: () => void
  children: ReactNode
  rodape?: ReactNode
  largura?: number
  tela?: boolean
  fecharAoClicarFora?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  const corpo = useRef<HTMLDivElement>(null)
  const [rolavel, setRolavel] = useState(false)
  const idTitulo = useId()
  const idDescricao = useId()

  // Regiões que rolam precisam ser alcançáveis pelo teclado (WCAG 2.1.1); só viram parada de Tab se rolarem.
  useEffect(() => {
    const el = corpo.current
    if (!el) return
    const medir = () => setRolavel(el.scrollHeight > el.clientHeight + 1)
    medir()
    const obs = new ResizeObserver(medir)
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  useEffect(() => {
    const anterior = document.activeElement as HTMLElement | null
    const el = ref.current
    const alvo = el?.querySelector<HTMLElement>('[data-autofocus]') ?? el?.querySelector<HTMLElement>(FOCAVEIS)
    ;(alvo ?? el)?.focus()
    return () => anterior?.focus?.()
  }, [])

  function aoTeclar(e: React.KeyboardEvent) {
    if (e.key === 'Escape') {
      e.stopPropagation()
      onClose()
      return
    }
    if (e.key !== 'Tab' || !ref.current) return
    const itens = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCAVEIS)).filter((x) => x.offsetParent !== null)
    if (itens.length === 0) return
    const primeiro = itens[0]
    const ultimo = itens[itens.length - 1]
    if (e.shiftKey && document.activeElement === primeiro) { e.preventDefault(); ultimo.focus() }
    else if (!e.shiftKey && document.activeElement === ultimo) { e.preventDefault(); primeiro.focus() }
  }

  return createPortal(
    <div className="dialog-fundo" onMouseDown={(e) => { if (fecharAoClicarFora && e.target === e.currentTarget) onClose() }}>
      <div
        ref={ref} role="dialog" aria-modal="true" aria-labelledby={idTitulo}
        aria-describedby={descricao ? idDescricao : undefined} tabIndex={-1}
        className={`dialog ${tela ? 'dialog-tela' : ''}`} style={tela ? undefined : { maxWidth: largura }}
        onKeyDown={aoTeclar}
      >
        <header className="dialog-topo">
          <h2 id={idTitulo}>{titulo}</h2>
          <button className="btn-icone" aria-label="Fechar" onClick={onClose}><Icon name="x" /></button>
        </header>
        {descricao && <p id={idDescricao} className="dialog-descricao">{descricao}</p>}
        <div className="dialog-corpo" ref={corpo}
          {...(rolavel ? { tabIndex: 0, role: 'region', 'aria-label': `Conteúdo de ${titulo}` } : {})}>{children}</div>
        {rodape && <footer className="dialog-rodape">{rodape}</footer>}
      </div>
    </div>,
    document.body,
  )
}

export function Confirmar({
  titulo, mensagem, rotuloConfirmar, perigo, onConfirmar, onCancelar,
}: {
  titulo: string; mensagem: ReactNode; rotuloConfirmar: string; perigo?: boolean
  onConfirmar: () => void; onCancelar: () => void
}) {
  return (
    <Dialog titulo={titulo} onClose={onCancelar} largura={460}
      rodape={<>
        <button className="btn" onClick={onCancelar} data-autofocus>Cancelar</button>
        <button className={`btn ${perigo ? 'btn-perigo' : 'btn-primario'}`} onClick={onConfirmar}>{rotuloConfirmar}</button>
      </>}>
      <p>{mensagem}</p>
    </Dialog>
  )
}

// ------------------------------------------------------------------ peças visuais
export function EstadoBadge({ estado, compacto }: { estado: Estado; compacto?: boolean }) {
  return (
    <span className={`estado estado-${estado}`}>
      <Icon name={ICONE_ESTADO[estado]} size={compacto ? 12 : 14} className={estado === 'executando' ? 'girar' : undefined} />
      <span>{ROTULO_ESTADO[estado]}</span>
    </span>
  )
}

export function TipoChip({ tipo }: { tipo: TipoDado }) {
  return (
    <span className={`tipo-chip tipo-${tipo}`} title={`Tipo: ${ROTULO_TIPO[tipo]}`}>
      <span className={`forma forma-${FORMA_TIPO[tipo]}`} aria-hidden="true" />
      {ROTULO_TIPO[tipo]}
    </span>
  )
}

export function Aviso({ tipo = 'info', titulo, children }: { tipo?: 'info' | 'aviso' | 'erro' | 'sucesso'; titulo?: string; children?: ReactNode }) {
  return (
    <div className={`aviso aviso-${tipo}`} role={tipo === 'erro' ? 'alert' : undefined}>
      <Icon name={tipo === 'erro' || tipo === 'aviso' ? 'alert' : tipo === 'sucesso' ? 'check' : 'info'} />
      <div>
        {titulo && <strong>{titulo}</strong>}
        {children && <div>{children}</div>}
      </div>
    </div>
  )
}

export function Detalhes({ resumo, children, aberto }: { resumo: string; children: ReactNode; aberto?: boolean }) {
  return (
    <details className="detalhes" open={aberto}>
      <summary>{resumo}</summary>
      <div className="detalhes-corpo">{children}</div>
    </details>
  )
}

// ------------------------------------------------------------------ menu de ações (⋯)
export interface ItemDeMenu {
  rotulo: string
  icone?: string
  perigo?: boolean
  desabilitado?: boolean
  onClick: () => void
}

/** Menu acessível: abre com Enter/Espaço/Seta para baixo, navega com as setas e fecha com Esc, Tab ou clique fora. */
export function MenuDeAcoes({ rotulo, itens, classe }: { rotulo: string; itens: ItemDeMenu[]; classe?: string }) {
  const [aberto, setAberto] = useState(false)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const botao = useRef<HTMLButtonElement>(null)
  const lista = useRef<HTMLUListElement>(null)
  const id = useId()

  useLayoutEffect(() => {
    if (!aberto || !botao.current) return
    const r = botao.current.getBoundingClientRect()
    const largura = 220
    setPos({ top: r.bottom + 4, left: Math.max(8, Math.min(window.innerWidth - largura - 8, r.right - largura)) })
  }, [aberto])

  // o menu só existe depois que a posição é calculada; só então o foco pode ir para o primeiro item
  useEffect(() => {
    if (aberto && pos) lista.current?.querySelector<HTMLElement>('[role="menuitem"]:not([disabled])')?.focus()
  }, [aberto, pos])

  useEffect(() => {
    if (!aberto) return
    const fora = (e: MouseEvent) => {
      const alvo = e.target as Node
      if (!lista.current?.contains(alvo) && !botao.current?.contains(alvo)) setAberto(false)
    }
    const rolagem = () => setAberto(false)
    document.addEventListener('mousedown', fora)
    window.addEventListener('scroll', rolagem, true)
    window.addEventListener('resize', rolagem)
    return () => {
      document.removeEventListener('mousedown', fora)
      window.removeEventListener('scroll', rolagem, true)
      window.removeEventListener('resize', rolagem)
    }
  }, [aberto])

  function fechar(devolverFoco = true) {
    setAberto(false)
    setPos(null)
    if (devolverFoco) botao.current?.focus()
  }

  function aoTeclarNoMenu(e: React.KeyboardEvent) {
    const itensEl = Array.from(lista.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])') ?? [])
    const i = itensEl.indexOf(document.activeElement as HTMLElement)
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); fechar() }
    else if (e.key === 'Tab') setAberto(false)
    else if (e.key === 'ArrowDown') { e.preventDefault(); itensEl[(i + 1) % itensEl.length]?.focus() }
    else if (e.key === 'ArrowUp') { e.preventDefault(); itensEl[(i - 1 + itensEl.length) % itensEl.length]?.focus() }
    else if (e.key === 'Home') { e.preventDefault(); itensEl[0]?.focus() }
    else if (e.key === 'End') { e.preventDefault(); itensEl[itensEl.length - 1]?.focus() }
  }

  return (
    <>
      <button ref={botao} type="button" className={`btn-icone ${classe ?? ''}`} aria-label={rotulo} aria-haspopup="menu" aria-expanded={aberto}
        aria-controls={aberto ? id : undefined} onClick={(e) => { e.stopPropagation(); setAberto((v) => !v) }}
        onKeyDown={(e) => { if (e.key === 'ArrowDown') { e.preventDefault(); setAberto(true) } }}>
        <Icon name="more" size={18} />
      </button>
      {aberto && pos && createPortal(
        <ul ref={lista} id={id} role="menu" aria-label={rotulo} className="menu" style={{ top: pos.top, left: pos.left }} onKeyDown={aoTeclarNoMenu}>
          {itens.map((it) => (
            <li key={it.rotulo} role="none">
              <button type="button" role="menuitem" disabled={it.desabilitado} className={it.perigo ? 'menu-perigo' : undefined}
                onClick={() => { fechar(false); it.onClick() }}>
                {it.icone && <Icon name={it.icone} size={16} />} {it.rotulo}
              </button>
            </li>
          ))}
        </ul>,
        document.body,
      )}
    </>
  )
}
