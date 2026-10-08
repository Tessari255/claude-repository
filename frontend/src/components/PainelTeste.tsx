import { useEffect, useMemo, useState } from 'react'
import { valorPadraoDoTipo } from '../lib/modelo'
import { formatarData, formatarDuracao, formatarValor } from '../lib/visual'
import type { PortDef, Run } from '../types'
import { Icon } from './Icons'
import { Aviso, EstadoBadge } from './ui'
import { ValueField } from './ValueField'

/** Painel “Testar”: manualmente (preenche os campos do gatilho) ou automaticamente (com os dados de uma execução anterior). */
export function PainelTeste({
  campos, run, executando, historico, executorMsg, onTestar, onCancelar, onFechar, onVerNoFluxo, iniciarAoAbrir,
}: {
  campos: PortDef[]
  run: Run | null
  executando: boolean
  historico: Run[]
  executorMsg: string | null
  onTestar: (dados: Record<string, unknown> | undefined) => void
  onCancelar: () => void
  onFechar: () => void
  onVerNoFluxo: (stepId: string) => void
  iniciarAoAbrir: boolean
}) {
  const [modo, setModo] = useState<'manual' | 'automatico'>('manual')
  const [valores, setValores] = useState<Record<string, unknown>>({})
  const [anteriorId, setAnteriorId] = useState('')
  const anteriores = useMemo(() => historico.filter((h) => h.kind === 'fluxo' && h.state !== 'aguardando' && h.state !== 'executando'), [historico])

  useEffect(() => {
    const v: Record<string, unknown> = {}
    for (const c of campos) v[c.id] = c.default ?? valorPadraoDoTipo(c.type === 'qualquer' ? 'texto' : c.type)
    // o que o usuário já digitou é mantido quando o gatilho muda; campos novos entram com o valor padrão
    setValores((atual) => ({ ...v, ...Object.fromEntries(Object.entries(atual).filter(([k]) => k in v)) }))
  }, [campos])

  useEffect(() => {
    if (!anteriorId && anteriores[0]) setAnteriorId(anteriores[0].id)
  }, [anteriores, anteriorId])

  const faltando = campos.filter((c) => c.required && (c.default === undefined || c.default === null)
    && (valores[c.id] === '' || valores[c.id] === null || valores[c.id] === undefined))

  // Quem abre o painel pelo atalho (Ctrl+Enter) e não tem nada a preencher já começa o teste.
  useEffect(() => {
    if (iniciarAoAbrir && faltando.length === 0 && !executando && !run) iniciar()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function iniciar() {
    if (modo === 'automatico') {
      const r = anteriores.find((x) => x.id === anteriorId)
      onTestar(r ? r.trigger_inputs : undefined)
      return
    }
    // só envia o que o usuário preencheu ou o que tem padrão (campos opcionais vazios ficam de fora)
    const dados: Record<string, unknown> = {}
    for (const c of campos) {
      const v = valores[c.id]
      if (v === '' || v === null || v === undefined) continue
      dados[c.id] = v
    }
    onTestar(dados)
  }

  const nomeDoErro = run?.error?.step_name
  return (
    <aside className="painel-direito" aria-label="Testar o fluxo">
      <div className="painel-topo">
        <h2><Icon name="play" size={18} /> Testar o fluxo</h2>
        <button className="btn-icone" aria-label="Fechar o painel de teste" onClick={onFechar}><Icon name="x" /></button>
      </div>
      <div className="painel-corpo">
        {executorMsg && <Aviso tipo="aviso" titulo="Código Python desabilitado">{executorMsg} Os passos com Python não vão rodar.</Aviso>}
        <fieldset className="campo modo-teste" disabled={executando}>
          <legend className="rotulo-campo">Como testar</legend>
          <label className="checagem"><input type="radio" name="modo-teste" checked={modo === 'manual'} onChange={() => setModo('manual')} /> Manualmente</label>
          <label className="checagem"><input type="radio" name="modo-teste" checked={modo === 'automatico'} disabled={anteriores.length === 0} onChange={() => setModo('automatico')} />
            Com os dados de uma execução anterior</label>
        </fieldset>

        {modo === 'manual' ? (
          <fieldset disabled={executando} className="campos-gatilho">
            <legend className="rotulo-campo">Dados do gatilho</legend>
            {campos.length === 0 && <p className="campo-ajuda">O gatilho deste fluxo não pede nenhum dado. Você pode declarar campos nele.</p>}
            {campos.map((c) => (
              <div key={c.id} className="campo-teste">
                <ValueField tipo={c.type} rotulo={c.label} obrigatorio={c.required && (c.default === undefined || c.default === null)} valor={valores[c.id]}
                  ajuda={c.description || (c.default !== undefined && c.default !== null ? 'Já vem preenchido com o valor padrão.' : undefined)}
                  onChange={(v) => setValores((x) => ({ ...x, [c.id]: v }))} />
              </div>
            ))}
          </fieldset>
        ) : (
          <div className="campo">
            <label htmlFor="execucao-anterior">Execução anterior</label>
            <select id="execucao-anterior" value={anteriorId} disabled={executando} onChange={(e) => setAnteriorId(e.target.value)}>
              {anteriores.map((r) => <option key={r.id} value={r.id}>{formatarData(r.started_at ?? r.created_at)} — {r.state === 'concluido' ? 'concluída' : r.state === 'falhou' ? 'falhou' : r.state}</option>)}
            </select>
            <p className="campo-ajuda">Usa os mesmos dados do gatilho daquela execução.</p>
          </div>
        )}

        <div className="acoes-linha">
          {executando
            ? <button className="btn btn-perigo-suave" onClick={onCancelar}><Icon name="stop" size={16} /> Cancelar a execução</button>
            : <button className="btn btn-primario" onClick={iniciar} disabled={modo === 'manual' && faltando.length > 0} data-autofocus>
                <Icon name="play" size={16} /> {run ? 'Testar de novo' : 'Testar'}
              </button>}
          {modo === 'manual' && faltando.length > 0 && !executando && <span className="campo-ajuda">Preencha: {faltando.map((c) => c.label).join(', ')}.</span>}
        </div>

        {run && (
          <section className="resultado" aria-label="Resultado do teste" aria-live="polite">
            <h3 className="secao">Resultado</h3>
            <div className="resumo-execucao">
              <EstadoBadge estado={run.state} />
              <span>{executando && !run.finished_at ? 'em andamento…' : formatarDuracao(run.duration_ms)}</span>
            </div>
            {run.state === 'falhou' && run.error && (
              <Aviso tipo="erro" titulo={`O fluxo parou em “${nomeDoErro}”`}>
                {run.error.message}
                {run.error.suggestion && <div className="campo-ajuda">{run.error.suggestion}</div>}
                <div className="acoes-linha"><button className="btn btn-pequeno" onClick={() => onVerNoFluxo(run.error!.step_id)}>Ver no fluxo</button></div>
              </Aviso>
            )}
            {run.result?.message && <Aviso tipo={run.state === 'falhou' ? 'erro' : 'info'}>{run.result.message}</Aviso>}
            {run.result?.outputs.map((o, i) => (
              <article key={`${o.step_id}-${i}`} className="saida-final">
                <h4>{o.title}</h4>
                {typeof o.value === 'string' ? <p className="saida-texto">{o.value}</p> : <pre className="valor">{formatarValor(o.value)}</pre>}
              </article>
            ))}
            {run.state === 'concluido' && run.result?.outputs.length === 0 && !run.result.message && (
              <Aviso tipo="info">O fluxo terminou, mas nenhum passo “Saída final” recebeu um valor para mostrar.</Aviso>
            )}
            {(run.state === 'aguardando' || run.state === 'executando') && <p className="campo-ajuda">Executando… o estado de cada passo aparece no fluxo.</p>}
          </section>
        )}
      </div>
    </aside>
  )
}
