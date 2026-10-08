import { useId } from 'react'
import { portasDeclaradas } from '../lib/modelo'
import { ROTULO_TIPO_LONGO, slugDeId } from '../lib/visual'
import type { PortDef, TipoDado } from '../types'
import { Icon } from './Icons'
import { ValueField } from './ValueField'

const TIPOS: TipoDado[] = ['texto', 'numero', 'booleano', 'lista', 'json']
const RE_ID = /^[a-z_][a-z0-9_]{0,39}$/

export interface Renomeacao { de: string; para: string }

/** Remove acentos e símbolos para virar um identificador (usado enquanto o usuário digita o rótulo). */
function idUnico(base: string, usados: Set<string>): string {
  const b = slugDeId(base) || 'campo'
  if (!usados.has(b)) return b
  let n = 2
  while (usados.has(`${b}_${n}`)) n++
  return `${b}_${n}`
}

/**
 * Declaração de campos: os campos de entrada do gatilho, ou as entradas/saídas de um passo de código Python.
 * `modo` decide o que cada linha oferece (obrigatório e valor padrão só fazem sentido para entradas).
 */
export function PortasEditor({
  portas, modo, titulo, ajuda, erro, desabilitado, onChange,
}: {
  portas: unknown
  modo: 'gatilho' | 'entradas' | 'saidas'
  titulo: string
  ajuda?: string
  erro?: string | null
  desabilitado?: boolean
  onChange: (portas: PortDef[], renomeacoes: Renomeacao[]) => void
}) {
  const lista = portasDeclaradas(portas)
  const base = useId()
  const rotuloItem = modo === 'saidas' ? 'Saída' : modo === 'entradas' ? 'Entrada' : 'Campo'

  function atualizar(i: number, parcial: Partial<PortDef>, renomear?: Renomeacao) {
    onChange(lista.map((p, k) => (k === i ? { ...p, ...parcial } : p)), renomear ? [renomear] : [])
  }

  function aoMudarRotulo(i: number, rotulo: string) {
    const p = lista[i]
    // enquanto o nome técnico ainda é o que o rótulo anterior geraria, ele acompanha o rótulo
    const acompanha = !p.id || p.id === slugDeId(p.label) || /^campo(_\d+)?$/.test(p.id)
    if (!acompanha) return atualizar(i, { label: rotulo })
    const usados = new Set(lista.filter((_, k) => k !== i).map((x) => x.id))
    const novoId = idUnico(rotulo, usados)
    atualizar(i, { label: rotulo, id: novoId }, novoId !== p.id && p.id ? { de: p.id, para: novoId } : undefined)
  }

  function adicionar() {
    const usados = new Set(lista.map((p) => p.id))
    const id = idUnico(modo === 'saidas' ? 'resultado' : 'campo', usados)
    onChange([...lista, { id, label: id.charAt(0).toUpperCase() + id.slice(1).replace(/_/g, ' '), type: 'texto', required: modo !== 'gatilho', description: '' }], [])
  }

  return (
    <fieldset className="portas" aria-label={titulo}>
      <legend className="secao-legenda">{titulo}</legend>
      {ajuda && <p className="campo-ajuda">{ajuda}</p>}
      {lista.length === 0 && <p className="vazio">{modo === 'saidas' ? 'Declare ao menos uma saída.' : 'Nenhum campo ainda.'}</p>}
      {lista.map((p, i) => {
        const idValido = RE_ID.test(p.id)
        const repetido = lista.some((o, k) => k !== i && o.id === p.id)
        return (
          <div className="linha-declaracao" key={i} role="group" aria-label={`${rotuloItem} ${i + 1}`}>
            <div className="grade-2">
              <div className="campo">
                <label htmlFor={`${base}-r-${i}`}>Rótulo</label>
                <input id={`${base}-r-${i}`} type="text" value={p.label} maxLength={60} disabled={desabilitado} onChange={(e) => aoMudarRotulo(i, e.target.value)} />
              </div>
              <div className="campo">
                <label htmlFor={`${base}-t-${i}`}>Tipo</label>
                <select id={`${base}-t-${i}`} value={p.type} disabled={desabilitado} onChange={(e) => atualizar(i, { type: e.target.value as TipoDado, default: undefined })}>
                  {TIPOS.map((t) => <option key={t} value={t}>{ROTULO_TIPO_LONGO[t]}</option>)}
                </select>
              </div>
            </div>
            <div className="campo">
              <label htmlFor={`${base}-n-${i}`}>Nome {modo === 'gatilho' ? 'técnico' : 'no código'}</label>
              <input id={`${base}-n-${i}`} type="text" className="mono" value={p.id} maxLength={40} disabled={desabilitado}
                aria-invalid={!idValido || repetido ? true : undefined}
                onChange={(e) => atualizar(i, { id: e.target.value }, p.id && e.target.value && RE_ID.test(e.target.value) ? { de: p.id, para: e.target.value } : undefined)} />
              {(!idValido || repetido) && (
                <p className="campo-erro">{repetido ? 'Este nome já está em uso.' : 'Use letras minúsculas, números e _, começando por letra.'}</p>
              )}
              {modo !== 'gatilho' && idValido && !repetido && (
                <p className="campo-ajuda">No código: <code>{modo === 'saidas' ? `return {"${p.id}": …}` : `inputs["${p.id}"]`}</code></p>
              )}
            </div>
            {modo !== 'saidas' && (
              <>
              <label className="checagem">
                <input type="checkbox" checked={p.required} disabled={desabilitado} onChange={(e) => atualizar(i, { required: e.target.checked })} />
                {modo === 'gatilho' ? 'Obrigatório ao testar' : 'Obrigatória'}
              </label>
              {modo === 'entradas' && <p className="campo-ajuda">Se desmarcada, a entrada só aparece em <code>inputs</code> quando for preenchida.</p>}
              </>
            )}
            {modo === 'gatilho' && (
              <div className="campo">
                <label className="checagem">
                  <input type="checkbox" checked={p.default !== undefined && p.default !== null} disabled={desabilitado}
                    onChange={(e) => atualizar(i, { default: e.target.checked ? (p.type === 'numero' ? 0 : p.type === 'booleano' ? false : p.type === 'lista' ? [] : p.type === 'json' ? {} : '') : undefined })} />
                  Usar um valor padrão
                </label>
                {p.default !== undefined && p.default !== null && (
                  <ValueField tipo={p.type} rotulo={`Valor padrão de ${p.label}`} valor={p.default} semRotulo onChange={(v) => atualizar(i, { default: v })} desabilitado={desabilitado} />
                )}
              </div>
            )}
            {!desabilitado && (
              <button type="button" className="btn btn-pequeno btn-perigo-suave" onClick={() => onChange(lista.filter((_, k) => k !== i), [])}>
                <Icon name="trash" size={14} /> Remover {rotuloItem.toLowerCase()} {i + 1}
              </button>
            )}
          </div>
        )
      })}
      {!desabilitado && lista.length < 20 && (
        <button type="button" className="btn btn-pequeno" onClick={adicionar}><Icon name="plus" size={14} /> Adicionar {modo === 'saidas' ? 'saída' : modo === 'entradas' ? 'entrada' : 'campo'}</button>
      )}
      {erro && <p className="campo-erro" role="alert">{erro}</p>}
    </fieldset>
  )
}
