import { formatarDuracao, formatarValor } from '../lib/visual'
import type { Run } from '../types'
import { Icon } from './Icons'
import { Aviso, Detalhes, EstadoBadge } from './ui'

const ROTULO_ORIGEM = { stdout: 'saída', stderr: 'erro', system: 'sistema' } as const

/** Resultado de um teste isolado de bloco: saídas, logs e erro (com linha e detalhes técnicos). */
export function ResultadoDoTeste({ run }: { run: Run }) {
  const passo = run.steps[0]
  const erro = passo?.error
  return (
    <div className="resultado-teste" aria-live="polite">
      <div className="resumo-execucao">
        <EstadoBadge estado={run.state} />
        <span>Duração: {formatarDuracao(run.duration_ms)}</span>
      </div>
      {erro && (
        <Aviso tipo="erro" titulo={erro.technical?.line ? `Erro na linha ${erro.technical.line}` : 'Erro no bloco'}>
          <p>{erro.message}</p>
          {erro.suggestion && <p className="campo-ajuda"><strong>O que fazer:</strong> {erro.suggestion}</p>}
          {erro.technical && (
            <Detalhes resumo="Detalhes técnicos">
              {erro.technical.snippet && <p>Trecho: <code>{erro.technical.snippet}</code></p>}
              {erro.technical.type && <p>Tipo: <code>{erro.technical.type}</code> — <code>{erro.technical.message}</code></p>}
              {erro.technical.traceback && <pre className="valor">{erro.technical.traceback}</pre>}
            </Detalhes>
          )}
        </Aviso>
      )}
      {passo?.outputs && (
        <>
          <h4><Icon name="check" size={14} /> Saídas</h4>
          <pre className="valor">{formatarValor(passo.outputs)}</pre>
        </>
      )}
      {passo && passo.logs.length > 0 && (
        <>
          <h4>Logs</h4>
          {passo.logs.map((l, i) => <pre key={i} className={`log log-${l.source}`}><span className="log-origem">{ROTULO_ORIGEM[l.source]}</span>{l.text}</pre>)}
        </>
      )}
    </div>
  )
}
