import { useEffect, useId, useRef, useState } from 'react'
import { CodeEditor } from './CodeEditor'

export interface Opcao { value: string; label: string }

interface Props {
  tipo: string // texto | numero | booleano | lista | json | qualquer | selecao | codigo
  valor: unknown
  onChange: (v: unknown) => void
  rotulo: string
  opcoes?: Opcao[]
  multilinha?: boolean
  placeholder?: string
  ajuda?: string
  obrigatorio?: boolean
  erro?: string | null
  desabilitado?: boolean
  /** O rótulo já é mostrado por quem usa o campo: aqui ele só nomeia o controle para leitores de tela. */
  semRotulo?: boolean
}

const DICA_JSON: Record<string, string> = {
  lista: 'Digite uma lista JSON, por exemplo: [1, 2, 3]',
  json: 'Digite um objeto JSON, por exemplo: {"nome": "Ana"}',
  qualquer: 'Digite JSON: "texto" entre aspas, 42, true, [1, 2] ou {"a": 1}',
}

function analisar(texto: string, tipo: string): { ok: true; valor: unknown } | { ok: false; mensagem: string } {
  try {
    const v = JSON.parse(texto)
    if (tipo === 'lista' && !Array.isArray(v)) return { ok: false, mensagem: 'Precisa ser uma lista, como [1, 2, 3].' }
    if (tipo === 'json' && (v === null || typeof v !== 'object' || Array.isArray(v))) {
      return { ok: false, mensagem: 'Precisa ser um objeto JSON, como {"chave": "valor"}.' }
    }
    return { ok: true, valor: v }
  } catch {
    return { ok: false, mensagem: 'JSON inválido. ' + (DICA_JSON[tipo] ?? '') }
  }
}

export function ValueField({
  tipo, valor, onChange, rotulo, opcoes, multilinha, placeholder, ajuda, obrigatorio, erro, desabilitado, semRotulo,
}: Props) {
  const id = useId()
  const idAjuda = `${id}-ajuda`
  const idErro = `${id}-erro`
  const ehJson = tipo === 'lista' || tipo === 'json' || tipo === 'qualquer'
  const [rascunho, setRascunho] = useState(() => (ehJson ? JSON.stringify(valor ?? null, null, 2) : ''))
  const [erroLocal, setErroLocal] = useState<string | null>(null)
  const ultimo = useRef<string>(JSON.stringify(valor ?? null))

  // Quando o valor muda por fora (ex.: trocar de bloco), reflete no rascunho.
  useEffect(() => {
    if (!ehJson) return
    const atual = JSON.stringify(valor ?? null)
    if (atual !== ultimo.current) {
      ultimo.current = atual
      setRascunho(JSON.stringify(valor ?? null, null, 2))
      setErroLocal(null)
    }
  }, [valor, ehJson])

  const mensagemErro = erro ?? erroLocal
  const descricao = [ajuda ? idAjuda : '', mensagemErro ? idErro : ''].filter(Boolean).join(' ') || undefined
  const comum = {
    id, 'aria-describedby': descricao, 'aria-invalid': mensagemErro ? true : undefined,
    'aria-required': obrigatorio || undefined, disabled: desabilitado, 'aria-label': semRotulo ? rotulo : undefined,
  } as const

  let campo: JSX.Element
  if (tipo === 'codigo') {
    campo = <CodeEditor value={String(valor ?? '')} onChange={(v) => onChange(v)} ariaLabel={rotulo} altura={170} somenteLeitura={desabilitado} />
  } else if (tipo === 'selecao') {
    campo = (
      <select {...comum} value={String(valor ?? '')} onChange={(e) => onChange(e.target.value)}>
        {(opcoes ?? []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    )
  } else if (tipo === 'booleano') {
    campo = (
      <select {...comum} value={valor === true ? 'sim' : valor === false ? 'nao' : ''}
        onChange={(e) => onChange(e.target.value === 'sim')}>
        <option value="sim">Sim (verdadeiro)</option>
        <option value="nao">Não (falso)</option>
      </select>
    )
  } else if (tipo === 'numero') {
    campo = (
      <input {...comum} type="number" step="any" inputMode="decimal" placeholder={placeholder}
        value={typeof valor === 'number' ? valor : ''}
        onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))} />
    )
  } else if (ehJson) {
    campo = (
      <textarea {...comum} className="mono" rows={4} spellCheck={false} placeholder={DICA_JSON[tipo]} value={rascunho}
        onChange={(e) => {
          const texto = e.target.value
          setRascunho(texto)
          const r = analisar(texto, tipo)
          if (r.ok) { setErroLocal(null); ultimo.current = JSON.stringify(r.valor); onChange(r.valor) }
          else setErroLocal(r.mensagem)
        }} />
    )
  } else if (multilinha) {
    campo = <textarea {...comum} rows={3} placeholder={placeholder} value={String(valor ?? '')} onChange={(e) => onChange(e.target.value)} />
  } else {
    campo = <input {...comum} type="text" placeholder={placeholder} value={String(valor ?? '')} onChange={(e) => onChange(e.target.value)} />
  }

  return (
    <div className={semRotulo ? 'campo-simples' : 'campo'}>
      {!semRotulo && (
        <label htmlFor={tipo === 'codigo' ? undefined : id}>
          {rotulo}{obrigatorio && <span className="obrigatorio" aria-hidden="true"> *</span>}
        </label>
      )}
      {campo}
      {ajuda && <p id={idAjuda} className="campo-ajuda">{ajuda}</p>}
      {mensagemErro && <p id={idErro} className="campo-erro" role="alert">{mensagemErro}</p>}
    </div>
  )
}
