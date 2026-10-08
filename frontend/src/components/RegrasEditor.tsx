import type { ParamDef, Regra } from '../types'
import { CampoDinamicoField, type FonteDinamica } from './CampoDinamico'
import { Icon } from './Icons'

const UNARIOS = new Set(['vazio', 'nao_vazio', 'verdadeiro', 'falso'])
const MAX_REGRAS = 10

export function regrasDoParametro(valor: unknown): Regra[] {
  return Array.isArray(valor) ? (valor as Regra[]) : []
}

/** Editor de condições, como o construtor de condições do Power Automate: “valor — teste — valor”, juntando com E / OU. */
export function RegrasEditor({
  regras, combinador, def, fonte, erro, desabilitado, onRegras, onCombinador,
}: {
  regras: Regra[]
  combinador: string
  def: ParamDef
  fonte: FonteDinamica
  erro?: string | null
  desabilitado?: boolean
  onRegras: (r: Regra[]) => void
  onCombinador?: (c: string) => void
}) {
  const alterar = (i: number, parcial: Partial<Regra>) => onRegras(regras.map((r, k) => (k === i ? { ...r, ...parcial } : r)))
  const nova: Regra = { esq: { value: '' }, op: 'igual', dir: { value: '' } }
  return (
    <fieldset className="regras" aria-label={def.label}>
      <legend className="sr-only">{def.label}</legend>
      {regras.map((r, i) => {
        const unario = UNARIOS.has(r.op)
        const nome = regras.length > 1 ? `condição ${i + 1}` : 'condição'
        return (
          <div className="regra" key={i} role="group" aria-label={`Condição ${i + 1}`}>
            {i > 0 && <p className="regra-juncao">{combinador === 'ou' ? 'OU' : 'E'}</p>}
            <CampoDinamicoField rotulo={`Valor da ${nome}`} tipo="qualquer" campo={r.esq} fonte={fonte} desabilitado={desabilitado}
              onChange={(c) => alterar(i, { esq: c ?? { value: '' } })} placeholder="Escolha um valor" />
            <div className="campo">
              <label htmlFor={`op-${i}`} className="rotulo-campo">Teste da {nome}</label>
              <select id={`op-${i}`} value={r.op} disabled={desabilitado}
                onChange={(e) => {
                  const op = e.target.value
                  alterar(i, { op, dir: UNARIOS.has(op) ? undefined : (r.dir ?? { value: '' }) })
                }}>
                {def.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            {!unario && (
              <CampoDinamicoField rotulo={`Comparar com (${nome})`} tipo="qualquer" campo={r.dir ?? undefined} fonte={fonte} desabilitado={desabilitado}
                onChange={(c) => alterar(i, { dir: c ?? { value: '' } })} placeholder="Escolha ou digite um valor" />
            )}
            {regras.length > 1 && !desabilitado && (
              <button type="button" className="btn btn-pequeno btn-perigo-suave" onClick={() => onRegras(regras.filter((_, k) => k !== i))}>
                <Icon name="trash" size={14} /> Remover a {nome}
              </button>
            )}
          </div>
        )
      })}
      {regras.length > 1 && onCombinador && (
        <div className="campo">
          <label htmlFor="combinador" className="rotulo-campo">Juntar as condições com</label>
          <select id="combinador" value={combinador} disabled={desabilitado} onChange={(e) => onCombinador(e.target.value)}>
            <option value="e">E (todas precisam ser verdadeiras)</option>
            <option value="ou">OU (basta uma ser verdadeira)</option>
          </select>
        </div>
      )}
      {!desabilitado && regras.length < MAX_REGRAS && (
        <button type="button" className="btn btn-pequeno" onClick={() => onRegras([...regras, nova])}>
          <Icon name="plus" size={14} /> Adicionar condição
        </button>
      )}
      {erro && <p className="campo-erro" role="alert">{erro}</p>}
    </fieldset>
  )
}
