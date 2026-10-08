import {
  createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState,
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
