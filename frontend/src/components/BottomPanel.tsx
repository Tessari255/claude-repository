import { Fragment, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { ROTULO_ESTADO, formatarData, formatarDuracao, formatarValor } from '../lib/visual'
import type { Issue, Run, Step } from '../types'
import { classeOrigem, rotuloOrigem } from '../lib/visual'
import { Icon } from './Icons'
import { Aviso, Detalhes, EstadoBadge } from './ui'

export type Aba = 'resultado' | 'etapas' | 'logs' | 'erros' | 'problemas' | 'historico'

const ABAS: { id: Aba; rotulo: string; icone: string }[] = [
  { id: 'resultado', rotulo: 'Resultado', icone: 'flag' },
  { id: 'etapas', rotulo: 'Etapas', icone: 'loop' },
  { id: 'logs', rotulo: 'Logs', icone: 'text' },
  { id: 'erros', rotulo: 'Erros', icone: 'alert' },
  { id: 'problemas', rotulo: 'Problemas', icone: 'info' },
  { id: 'historico', rotulo: 'Histórico', icone: 'history' },
]


function Vazio({ icone, children }: { icone: string; children: React.ReactNode }) {
  return <div className="vazio-grande"><Icon name={icone} size={28} /><p>{children}</p></div>
}

function DetalheTecnico({ step }: { step: Step }) {
  const t = step.error?.technical
  if (!t) return null
  return (
    <Detalhes resumo="Detalhes técnicos">
      <dl className="tecnico">
        {t.type && <><dt>Tipo</dt><dd><code>{t.type}</code></dd></>}
        {t.message && <><dt>Mensagem original</dt><dd><code>{t.message}</code></dd></>}
        {t.line != null && <><dt>Linha</dt><dd>{t.line}</dd></>}
        {t.snippet && <><dt>Trecho</dt><dd><code>{t.snippet}</code></dd></>}
        {t.item_index != null && <><dt>Item da lista</dt><dd>{t.item_index + 1}</dd></>}
      </dl>
      {t.traceback && <pre className="valor">{t.traceback}</pre>}
    </Detalhes>
  )
}

export function BottomPanel({
  aba, onAba, run, executando, nomes, problemas, historico, onAbrirExecucao, onIrParaBloco,
  aberto, onAlternar, altura, onAltura, onExecutar, onLimpar,
}: {
  aba: Aba
  onAba: (a: Aba) => void
  run: Run | null
  executando: boolean
  nomes: Record<string, string>
  problemas: Issue[]
  historico: Run[]
  onAbrirExecucao: (id: string) => void
  onIrParaBloco: (id: string) => void
  aberto: boolean
  onAlternar: () => void
  altura: number
  onAltura: (n: number) => void
  onExecutar: () => void
  onLimpar: () => void
}) {
  const [expandidas, setExpandidas] = useState<Set<string>>(new Set())
  const arrasto = useRef<{ y: number; h: number } | null>(null)
  const falhas = run?.steps.filter((s) => s.state === 'falhou') ?? []
  const erros = problemas.filter((p) => p.severity === 'erro')
  const nome = (id: string) => nomes[id] ?? id

  function teclasDasAbas(e: KeyboardEvent<HTMLDivElement>) {
    const i = ABAS.findIndex((a) => a.id === aba)
    let novo = -1
    if (e.key === 'ArrowRight') novo = (i + 1) % ABAS.length
    else if (e.key === 'ArrowLeft') novo = (i - 1 + ABAS.length) % ABAS.length
    else if (e.key === 'Home') novo = 0
    else if (e.key === 'End') novo = ABAS.length - 1
    if (novo < 0) return
    e.preventDefault()
    onAba(ABAS[novo].id)
    document.getElementById(`aba-${ABAS[novo].id}`)?.focus()
  }

  function iniciarArrasto(e: PointerEvent<HTMLDivElement>) {
    arrasto.current = { y: e.clientY, h: altura }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  function arrastar(e: PointerEvent<HTMLDivElement>) {
    if (!arrasto.current) return
    onAltura(Math.max(120, Math.min(window.innerHeight * 0.7, arrasto.current.h + (arrasto.current.y - e.clientY))))
  }
  function teclasDoDivisor(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'ArrowUp') { e.preventDefault(); onAltura(Math.min(window.innerHeight * 0.7, altura + 32)) }
    if (e.key === 'ArrowDown') { e.preventDefault(); onAltura(Math.max(120, altura - 32)) }
  }

  function alternarEtapa(id: string) {
    setExpandidas((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  }

  const contagem = (a: Aba) => (a === 'erros' ? falhas.length : a === 'problemas' ? erros.length : 0)

  return (
    <section className="painel-inferior" aria-label="Resultados, logs e erros" style={{ height: aberto ? altura : undefined }}>
      {aberto && (
        <div
          role="separator" aria-orientation="horizontal" tabIndex={0} aria-label="Redimensionar a área de resultados (setas para cima e para baixo)"
          aria-valuenow={Math.round(altura)} aria-valuemin={120} aria-valuemax={Math.round(window.innerHeight * 0.7)}
          className="divisor" onPointerDown={iniciarArrasto} onPointerMove={arrastar}
          onPointerUp={() => { arrasto.current = null }} onKeyDown={teclasDoDivisor}
        />
      )}
      <div className="abas-barra">
        <div role="tablist" aria-label="Seções da área de resultados" className="abas" onKeyDown={teclasDasAbas}>
          {ABAS.map((a) => (
            <button key={a.id} role="tab" id={`aba-${a.id}`} aria-selected={aba === a.id} aria-controls={`painel-${a.id}`}
              tabIndex={aba === a.id ? 0 : -1} className="aba" onClick={() => { onAba(a.id); if (!aberto) onAlternar() }}>
              <Icon name={a.icone} size={15} /> {a.rotulo}
              {contagem(a.id) > 0 && <span className="contagem" aria-label={`${contagem(a.id)} itens`}>{contagem(a.id)}</span>}
            </button>
          ))}
        </div>
        {run && (
          <button className="btn btn-pequeno btn-fantasma" onClick={onLimpar} title="Remove o resultado da área de trabalho">
            <Icon name="eraser" size={14} /> Limpar resultado
          </button>
        )}
        <button className="btn-icone" onClick={onAlternar} aria-expanded={aberto}
          aria-label={aberto ? 'Recolher a área de resultados' : 'Expandir a área de resultados'}>
          <Icon name="chevronDown" className={aberto ? '' : 'girar-180'} />
        </button>
      </div>

      {aberto && (
        <div className="abas-conteudo" role="tabpanel" id={`painel-${aba}`} aria-labelledby={`aba-${aba}`} tabIndex={0}>
          {aba === 'resultado' && (
            !run ? (
              <Vazio icone="play">
                Nenhum resultado ainda. Clique em <button className="link" onClick={onExecutar}>Executar</button> (ou use Ctrl+Enter)
                para rodar o fluxo.
              </Vazio>
            ) : (
              <div className="resultado">
                <div className="resumo-execucao">
                  <EstadoBadge estado={run.state} />
                  <span>{run.kind === 'bloco' ? 'Teste de bloco' : 'Execução'} <code>{run.id}</code></span>
                  <span>Duração: {executando && !run.finished_at ? 'em andamento…' : formatarDuracao(run.duration_ms)}</span>
                  <span>Início: {formatarData(run.started_at ?? run.created_at)}</span>
                </div>
                {run.state === 'falhou' && run.error && (
                  <Aviso tipo="erro" titulo={`O fluxo parou no bloco “${run.error.block_name}”`}>
                    {run.error.message}
                    {run.error.suggestion && <div className="campo-ajuda">{run.error.suggestion}</div>}
                    <div className="acoes-linha">
                      <button className="btn btn-pequeno" onClick={() => onAba('erros')}>Ver detalhes do erro</button>
                      <button className="btn btn-pequeno" onClick={() => onIrParaBloco(run.error!.block_id)}>Ir para o bloco</button>
                    </div>
                  </Aviso>
                )}
                {run.result?.outputs.map((o) => (
                  <article key={o.block_id} className="saida-final">
                    <h3>{o.title}</h3>
                    {typeof o.value === 'string'
                      ? <p className="saida-texto">{o.value}</p>
                      : <pre className="valor">{formatarValor(o.value)}</pre>}
                  </article>
                ))}
                {run.state === 'concluido' && run.result?.outputs.length === 0 && (
                  <Aviso tipo="info">O fluxo terminou, mas nenhum bloco “Saída final” recebeu um valor para mostrar.</Aviso>
                )}
                {(run.state === 'aguardando' || run.state === 'executando') && <p className="campo-ajuda">Executando… acompanhe cada bloco na aba “Etapas”.</p>}
              </div>
            )
          )}

          {aba === 'etapas' && (
            !run ? <Vazio icone="loop">As etapas de cada bloco aparecem aqui depois que você executar o fluxo.</Vazio> : (
              <table className="tabela">
                <caption className="sr-only">Etapas da execução, na ordem em que foram processadas</caption>
                <thead><tr><th scope="col">#</th><th scope="col">Bloco</th><th scope="col">Estado</th><th scope="col">Duração</th><th scope="col"><span className="sr-only">Detalhes</span></th></tr></thead>
                <tbody>
                  {run.steps.map((s, i) => {
                    const aberta = expandidas.has(s.block_id)
                    return (
                      <Fragment key={s.block_id}>
                        <tr className={`linha-${s.state}`}>
                          <td>{i + 1}</td>
                          <td><button className="link" onClick={() => onIrParaBloco(s.block_id)}>{nome(s.block_id)}</button></td>
                          <td><EstadoBadge estado={s.state} compacto /></td>
                          <td>{s.state === 'ignorado' ? '—' : formatarDuracao(s.duration_ms)}</td>
                          <td>
                            <button className="btn-icone" aria-expanded={aberta} aria-label={`${aberta ? 'Ocultar' : 'Mostrar'} detalhes de ${nome(s.block_id)}`}
                              onClick={() => alternarEtapa(s.block_id)}>
                              <Icon name={aberta ? 'chevronDown' : 'chevronRight'} size={16} />
                            </button>
                          </td>
                        </tr>
                        {aberta && (
                          <tr className="linha-detalhe">
                            <td colSpan={5}>
                              {s.skip_reason && <p><strong>Ignorado:</strong> {s.skip_reason}</p>}
                              {s.error && <Aviso tipo="erro">{s.error.message}</Aviso>}
                              <div className="grade-es">
                                <div><h4>Entradas</h4><pre className="valor">{s.inputs ? formatarValor(s.inputs) : '—'}</pre></div>
                                <div><h4>Saídas</h4><pre className="valor">{s.outputs ? formatarValor(s.outputs) : '—'}</pre></div>
                              </div>
                              {s.logs.length > 0 && <><h4>Logs</h4>{s.logs.map((l, k) => <pre key={k} className={`log log-${classeOrigem(l.source)}`}><span className="log-origem">{rotuloOrigem(l.source)}</span>{l.text}</pre>)}</>}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            )
          )}

          {aba === 'logs' && (
            !run ? <Vazio icone="text">Os logs (o que o código imprime com print) aparecem aqui.</Vazio> : (
              run.steps.some((s) => s.logs.length > 0) ? (
                <div className="logs">
                  {run.steps.filter((s) => s.logs.length > 0).map((s) => (
                    <section key={s.block_id}>
                      <h3>{nome(s.block_id)} <EstadoBadge estado={s.state} compacto /></h3>
                      {s.logs.map((l, i) => (
                        <pre key={i} className={`log log-${classeOrigem(l.source)}`}><span className="log-origem">{rotuloOrigem(l.source)}</span>{l.text}</pre>
                      ))}
                    </section>
                  ))}
                </div>
              ) : <Vazio icone="text">Nenhum bloco gerou logs nesta execução.</Vazio>
            )
          )}

          {aba === 'erros' && (
            falhas.length === 0 ? (
              <Vazio icone="check">{run ? 'Nenhum erro nesta execução.' : 'Os erros aparecem aqui, explicados em linguagem simples.'}</Vazio>
            ) : (
              <div className="erros">
                {falhas.map((s) => (
                  <article key={s.block_id} className="erro-cartao">
                    <h3><Icon name="alert" /> Erro no bloco “{nome(s.block_id)}”</h3>
                    <p className="erro-mensagem">{s.error?.message}</p>
                    {s.error?.suggestion && <p className="erro-sugestao"><strong>O que fazer:</strong> {s.error.suggestion}</p>}
                    <DetalheTecnico step={s} />
                    <button className="btn btn-pequeno" onClick={() => onIrParaBloco(s.block_id)}>Ir para o bloco</button>
                  </article>
                ))}
              </div>
            )
          )}

          {aba === 'problemas' && (
            problemas.length === 0 ? <Vazio icone="check">Nenhum problema encontrado. O fluxo está pronto para executar.</Vazio> : (
              <ul className="problemas">
                {problemas.map((p, i) => (
                  <li key={i} className={`problema problema-${p.severity}`}>
                    <Icon name={p.severity === 'erro' ? 'alert' : 'info'} />
                    <div>
                      <p><strong>{p.severity === 'erro' ? 'Erro' : 'Aviso'}:</strong> {p.message}</p>
                      {p.hint && <p className="campo-ajuda">{p.hint}</p>}
                      {p.block_id && <button className="btn btn-pequeno" onClick={() => onIrParaBloco(p.block_id!)}>Ir para o bloco</button>}
                    </div>
                  </li>
                ))}
              </ul>
            )
          )}

          {aba === 'historico' && (
            historico.length === 0 ? <Vazio icone="history">As execuções anteriores deste projeto aparecem aqui.</Vazio> : (
              <table className="tabela">
                <caption className="sr-only">Execuções anteriores deste projeto</caption>
                <thead><tr><th scope="col">Quando</th><th scope="col">Tipo</th><th scope="col">Estado</th><th scope="col">Duração</th><th scope="col"><span className="sr-only">Ação</span></th></tr></thead>
                <tbody>
                  {historico.map((h) => (
                    <tr key={h.id} aria-current={run?.id === h.id ? 'true' : undefined}>
                      <td>{formatarData(h.started_at ?? h.created_at)}</td>
                      <td>{h.kind === 'bloco' ? 'Teste de bloco' : 'Fluxo'}</td>
                      <td><EstadoBadge estado={h.state} compacto /></td>
                      <td>{formatarDuracao(h.duration_ms)}</td>
                      <td><button className="btn btn-pequeno" onClick={() => onAbrirExecucao(h.id)}>Abrir</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          )}
        </div>
      )}
      <span className="sr-only">{run ? `Estado da execução: ${ROTULO_ESTADO[run.state]}` : ''}</span>
    </section>
  )
}
