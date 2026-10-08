import { formatarData, formatarDuracao } from '../lib/visual'
import type { Run } from '../types'
import { Icon } from './Icons'
import { EstadoBadge } from './ui'

/** Histórico de execuções do fluxo, como o “histórico de execuções” do Power Automate. */
export function Historico({
  runs, abertoId, onAbrir, onReenviar, onFechar, ocupado,
}: {
  runs: Run[]
  abertoId: string | null
  onAbrir: (r: Run) => void
  onReenviar: (r: Run) => void
  onFechar: () => void
  ocupado: boolean
}) {
  return (
    <aside className="painel-direito" aria-label="Histórico de execuções">
      <div className="painel-topo">
        <h2><Icon name="history" size={18} /> Histórico de execuções</h2>
        <button className="btn-icone" aria-label="Fechar o histórico" onClick={onFechar}><Icon name="x" /></button>
      </div>
      <div className="painel-corpo">
        {runs.length === 0 ? (
          <div className="vazio-grande"><Icon name="history" size={28} /><p>As execuções anteriores deste fluxo aparecem aqui. Clique em <strong>Testar</strong> para criar a primeira.</p></div>
        ) : (
          <table className="tabela tabela-historico">
            <caption className="sr-only">Execuções anteriores deste fluxo</caption>
            <thead><tr><th scope="col">Início</th><th scope="col">Duração</th><th scope="col">Estado</th><th scope="col"><span className="sr-only">Ações</span></th></tr></thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} aria-current={abertoId === r.id ? 'true' : undefined}>
                  <td>{formatarData(r.started_at ?? r.created_at)}</td>
                  <td>{formatarDuracao(r.duration_ms)}</td>
                  <td><EstadoBadge estado={r.state} compacto /></td>
                  <td className="acoes-tabela">
                    <button className="btn btn-pequeno" onClick={() => onAbrir(r)} aria-label={`Abrir a execução de ${formatarData(r.started_at ?? r.created_at)}`}>Abrir</button>
                    <button className="btn btn-pequeno" disabled={ocupado} onClick={() => onReenviar(r)} title="Executa de novo com os mesmos dados do gatilho"
                      aria-label={`Reenviar a execução de ${formatarData(r.started_at ?? r.created_at)}`}><Icon name="retry" size={14} /> Reenviar</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </aside>
  )
}
