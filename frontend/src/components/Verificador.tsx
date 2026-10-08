import type { Issue } from '../types'
import { Icon } from './Icons'

/** Verificador de fluxo: lista o que impede o fluxo de rodar (erros) e o que merece atenção (avisos). */
export function Verificador({
  problemas, nomeDe, onIr, onFechar, verificando,
}: {
  problemas: Issue[]
  nomeDe: (stepId: string) => string
  onIr: (stepId: string) => void
  onFechar: () => void
  verificando: boolean
}) {
  const erros = problemas.filter((p) => p.severity === 'erro')
  const avisos = problemas.filter((p) => p.severity === 'aviso')
  const grupo = (titulo: string, lista: Issue[], tipo: 'erro' | 'aviso') => lista.length > 0 && (
    <section aria-label={titulo}>
      <h3 className="secao">{titulo} ({lista.length})</h3>
      <ul className="problemas">
        {lista.map((p, i) => (
          <li key={i} className={`problema problema-${tipo}`}>
            <Icon name={tipo === 'erro' ? 'alert' : 'info'} />
            <div>
              <p><strong>{tipo === 'erro' ? 'Erro' : 'Aviso'}:</strong> {p.message}</p>
              {p.hint && <p className="campo-ajuda">{p.hint}</p>}
              {p.step_id && <button type="button" className="btn btn-pequeno" onClick={() => onIr(p.step_id!)}>Ir para “{nomeDe(p.step_id)}”</button>}
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
  return (
    <aside className="painel-direito" aria-label="Verificador de fluxo">
      <div className="painel-topo">
        <h2><Icon name="checker" size={18} /> Verificador de fluxo</h2>
        <button className="btn-icone" aria-label="Fechar o verificador" onClick={onFechar}><Icon name="x" /></button>
      </div>
      <div className="painel-corpo" aria-live="polite">
        {verificando && <p className="campo-ajuda" role="status">Verificando…</p>}
        {problemas.length === 0 && !verificando && (
          <div className="vazio-grande"><Icon name="check" size={28} /><p>Nenhum problema encontrado. O fluxo está pronto para testar.</p></div>
        )}
        {grupo('Erros', erros, 'erro')}
        {grupo('Avisos', avisos, 'aviso')}
        {erros.length > 0 && <p className="campo-ajuda">Erros impedem o teste do fluxo. Você pode salvar um rascunho com erros e voltar depois.</p>}
      </div>
    </aside>
  )
}
