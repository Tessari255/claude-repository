import { useMemo, useState } from 'react'
import {
  acharQualquer, atualizarPasso, conteudoDinamicoPara, definicaoEfetiva, nomeDoPasso, parametroVisivel, referenciasDe, renomearEntrada,
  renomearSaida, ROTULO_EXECUTAR_APOS, tipoDaEntrada, todosOsPassos, valorDoParametro, visiveisPara,
} from '../lib/modelo'
import { classeOrigem, corDaCategoria, formatarDuracao, formatarValor, iconeDoBloco, rotuloOrigem } from '../lib/visual'
import type {
  BlockType, ExecutarApos, Flow, Issue, ParamDef, Passo, PortTypes, Ref, Regra, Step, SystemInfo, TipoDado,
} from '../types'
import { CampoDinamicoField, type FonteDinamica } from './CampoDinamico'
import { Icon } from './Icons'
import { PortasEditor } from './PortasEditor'
import { RegrasEditor, regrasDoParametro } from './RegrasEditor'
import { Aviso, Detalhes, EstadoBadge } from './ui'
import { ValueField } from './ValueField'

type Aba = 'parametros' | 'configuracoes' | 'execucao'

export type Aplicar = (fn: (f: Flow) => Flow, coalescer?: string) => void

function semPrefixo(msg: string, nome: string) {
  return msg.startsWith(`${nome}: `) ? msg.slice(nome.length + 2) : msg
}

const TIPOS_DE_PYTHON = new Set(['builtin.python'])

function usaCodigoPython(def: BlockType, params: Record<string, unknown>) {
  return def.kind === 'python' || TIPOS_DE_PYTHON.has(def.id) || (def.id === 'builtin.transformar_lista' && params.operacao === 'python')
}

function DetalheTecnico({ passo }: { passo: Step }) {
  const t = passo.error?.technical
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

export function PainelPasso({
  passo, def, flow, defDe, portTypes, problemas, linha, iteracaoTexto, somenteLeitura, indice, sistema, ultimaVersao,
  aplicar, onFechar, onSalvarComoBloco, onAtualizarVersao, onEditarBloco,
}: {
  passo: Passo
  def: BlockType | undefined
  flow: Flow
  defDe: (p: Passo) => BlockType | undefined
  portTypes: PortTypes
  problemas: Issue[]
  linha: Step | null
  iteracaoTexto: string
  somenteLeitura: boolean
  indice: number
  sistema: SystemInfo | null
  ultimaVersao: number | null
  aplicar: Aplicar
  onFechar: () => void
  onSalvarComoBloco: (p: Passo) => void
  onAtualizarVersao: (p: Passo) => void
  onEditarBloco: (p: Passo) => void
}) {
  const [abaEscolhida, setAba] = useState<Aba>('parametros')
  const gatilho = passo.id === flow.trigger.id
  const aba: Aba = abaEscolhida === 'execucao' && !linha ? 'parametros' : abaEscolhida
  const nome = nomeDoPasso(passo, def)
  const atualizar = (fn: (p: Passo) => Passo, coalescer?: string) => aplicar((f) => atualizarPasso(f, passo.id, fn), coalescer)
  const meus = problemas.filter((i) => i.step_id === passo.id)

  const fonte: FonteDinamica = useMemo(() => {
    const tipos = new Map(todosOsPassos(flow).map((p) => [p.id, p]))
    return {
      grupos: def ? conteudoDinamicoPara(flow, passo.id, defDe, portTypes) : [],
      descrever: (r: Ref) => {
        const alvo = tipos.get(r.step)
        const d = alvo ? defDe(alvo) : undefined
        const ef = alvo && d ? definicaoEfetiva(d, alvo.params) : undefined
        const saida = ef?.outputs.find((o) => o.id === r.output)
        return { saida: saida?.label ?? r.output, passo: alvo ? nomeDoPasso(alvo, d) : r.step, valido: !!alvo && !!saida }
      },
    }
  }, [flow, passo.id, def, defDe, portTypes])

  if (!def) {
    return (
      <aside className="painel-direito" aria-label="Configuração do passo">
        <div className="painel-topo"><h2>Bloco indisponível</h2><button className="btn-icone" aria-label="Fechar" onClick={onFechar}><Icon name="x" /></button></div>
        <div className="painel-corpo">
          <Aviso tipo="erro" titulo={`${passo.type} (v${passo.version})`}>
            Este bloco não existe nesta instalação. Você ainda pode excluir o passo pelo menu do cartão.
          </Aviso>
        </div>
      </aside>
    )
  }

  const ef = definicaoEfetiva(def, passo.params)
  const completos: Record<string, unknown> = Object.fromEntries(def.params.map((p) => [p.id, valorDoParametro(passo.params, p)]))
  const erroDe = (campo: string) => {
    const e = meus.find((i) => i.field === campo && i.severity === 'erro')
    return e ? semPrefixo(e.message, nome) : null
  }
  const bloqueado = usaCodigoPython(def, completos) && !!sistema && !sistema.executor.disponivel

  // ------------------------------------------------------------------ alterações
  function mudarParametro(p: ParamDef, valor: unknown) {
    atualizar((x) => {
      const params = { ...x.params, [p.id]: valor }
      // entradas cujo tipo depende deste parâmetro (ex.: valor inicial da variável) voltam ao padrão do novo tipo
      const inputs = { ...x.inputs }
      for (const porta of def!.inputs) if (porta.type_from?.param === p.id && inputs[porta.id] && !('parts' in inputs[porta.id])) delete inputs[porta.id]
      return { ...x, params, inputs }
    }, `param:${passo.id}:${p.id}`)
  }

  function mudarCampo(portaId: string, campo: import('../types').Campo | undefined) {
    atualizar((x) => {
      const inputs = { ...x.inputs }
      if (campo === undefined) delete inputs[portaId]
      else inputs[portaId] = campo
      return { ...x, inputs }
    }, `campo:${passo.id}:${portaId}`)
  }

  function mudarPortas(p: ParamDef, valor: unknown, renomeacoes: { de: string; para: string }[]) {
    aplicar((f) => {
      let novo = atualizarPasso(f, passo.id, (x) => ({ ...x, params: { ...x.params, [p.id]: valor } }))
      for (const r of renomeacoes) {
        if (p.id === def!.inputs_from) novo = atualizarPasso(novo, passo.id, (x) => renomearEntrada(x, r.de, r.para))
        else novo = renomearSaida(novo, passo.id, r.de, r.para)
      }
      return novo
    }, `portas:${passo.id}:${p.id}`)
  }

  // ------------------------------------------------------------------ partes do formulário
  const visiveis = def.params.filter((p) => parametroVisivel(def, passo.params, p))
  const vis = visiveisPara(flow, passo.id, defDe)
  const variaveis = (vis?.antes ?? []).map((id) => acharQualquer(flow, id)).filter((x): x is Passo => !!x && x.type === 'builtin.var_inicializar')

  function renderParametro(p: ParamDef) {
    const valor = completos[p.id]
    if (p.type === 'regras') {
      return (
        <RegrasEditor key={p.id} regras={regrasDoParametro(valor)} combinador={String(completos.combinador ?? 'e')} def={p} fonte={fonte}
          erro={erroDe(p.id)} desabilitado={somenteLeitura}
          onRegras={(r: Regra[]) => mudarParametro(p, r)}
          onCombinador={(c) => mudarParametro(def!.params.find((x) => x.id === 'combinador')!, c)} />
      )
    }
    if (p.id === 'combinador' && def!.params.some((x) => x.type === 'regras')) return null
    if (p.type === 'portas') {
      const modo = p.id === def!.inputs_from ? 'entradas' : p.id === def!.outputs_from && def!.trigger ? 'gatilho' : 'saidas'
      return (
        <PortasEditor key={p.id} portas={valor} modo={modo} titulo={p.label} ajuda={p.help} erro={erroDe(p.id)} desabilitado={somenteLeitura}
          onChange={(portas, ren) => mudarPortas(p, portas, ren)} />
      )
    }
    if (p.type === 'variavel') {
      const atual = typeof valor === 'string' ? valor : ''
      const existe = variaveis.some((v) => v.id === atual)
      return (
        <div className="campo" key={p.id}>
          <label htmlFor={`param-${p.id}`}>{p.label}<span className="obrigatorio" aria-hidden="true"> *</span></label>
          <select id={`param-${p.id}`} value={atual} aria-invalid={erroDe(p.id) ? true : undefined} aria-required="true" disabled={somenteLeitura}
            onChange={(e) => mudarParametro(p, e.target.value)}>
            <option value="">— Escolha uma variável —</option>
            {variaveis.map((v) => <option key={v.id} value={v.id}>{String(v.params.nome ?? v.label ?? 'variável')} ({String(v.params.tipo ?? 'texto')})</option>)}
            {atual && !existe && <option value={atual}>(variável removida)</option>}
          </select>
          {p.help && <p className="campo-ajuda">{p.help}</p>}
          {variaveis.length === 0 && <p className="campo-ajuda">Ainda não há variáveis. Adicione “Inicializar variável” antes deste passo.</p>}
          {erroDe(p.id) && <p className="campo-erro" role="alert">{erroDe(p.id)}</p>}
        </div>
      )
    }
    const tipo = p.type === 'selecao' || p.type === 'codigo' ? p.type : p.type
    return (
      <ValueField
        key={`${passo.id}-${p.id}`} tipo={tipo} rotulo={p.label} valor={valor} opcoes={p.options} multilinha={p.multiline} placeholder={p.placeholder}
        ajuda={p.help} obrigatorio={p.required} erro={erroDe(p.id)} desabilitado={somenteLeitura} onChange={(v) => mudarParametro(p, v)}
      />
    )
  }

  const renderEntrada = (porta: import('../types').PortDef) => {
    const tipo = (portTypes[passo.id]?.inputs?.[porta.id] ?? tipoDaEntrada(def, passo.params, porta)) as TipoDado
    const obrigatoria = porta.required && (porta.default === undefined || porta.default === null)
    return (
      <CampoDinamicoField
        key={porta.id} rotulo={porta.label} tipo={tipo} campo={passo.inputs[porta.id]} fonte={fonte} obrigatorio={obrigatoria}
        ajuda={porta.description || undefined} erro={erroDe(porta.id)} desabilitado={somenteLeitura}
        multilinha={tipo === 'texto' && /mensagem|texto/i.test(porta.id) && def.id === 'builtin.encerrar'}
        onChange={(c) => mudarCampo(porta.id, c)}
      />
    )
  }

  const selecoes = visiveis.filter((p) => p.type === 'selecao' && p.id !== 'combinador')
  const demais = visiveis.filter((p) => p.type !== 'selecao' || p.id === 'combinador')
  const campos = (() => {
    if (def.id === 'builtin.python') {
      const por = (id: string) => visiveis.find((p) => p.id === id)!
      const resumo = `Entradas: ${ef.inputs.map((i) => i.id).join(', ') || 'nenhuma'} · Saídas: ${ef.outputs.map((o) => o.id).join(', ') || 'nenhuma'}`
      const comErro = !!erroDe('entradas') || !!erroDe('saidas') || ef.outputs.length === 0
      return (
        <>
          {ef.inputs.map(renderEntrada)}
          {renderParametro(por('codigo'))}
          <details className="detalhes declaracao-python" open={comErro || undefined}>
            <summary>Declarar entradas e saídas <span className="campo-ajuda">({resumo})</span></summary>
            <div className="detalhes-corpo">{renderParametro(por('entradas'))}{renderParametro(por('saidas'))}</div>
          </details>
        </>
      )
    }
    if (def.trigger) return <>{visiveis.map(renderParametro)}</>
    return <>{selecoes.map(renderParametro)}{ef.inputs.map(renderEntrada)}{demais.map(renderParametro)}</>
  })()

  const codigoAtivo = usaCodigoPython(def, completos)
  const referenciasQuebradas = Object.values(passo.inputs).flatMap(referenciasDe).filter((r) => !fonte.descrever(r).valido).length

  return (
    <aside className="painel-direito painel-passo" aria-label={`Configuração de ${nome}`}>
      <div className="painel-topo" style={{ ['--cor' as string]: corDaCategoria(def.category) }}>
        <span className="cartao-icone"><Icon name={iconeDoBloco(def.id, def.kind)} size={18} /></span>
        <div className="painel-titulo">
          <label className="sr-only" htmlFor="nome-do-passo">Nome do passo</label>
          <input id="nome-do-passo" type="text" className="titulo-editavel" value={passo.label ?? ''} placeholder={def.name} maxLength={80}
            disabled={somenteLeitura} onChange={(e) => atualizar((x) => ({ ...x, label: e.target.value || null }), `rotulo:${passo.id}`)} />
          <p className="painel-subtitulo">{gatilho ? 'Gatilho' : def.name}{def.kind === 'python' && ` · v${passo.version}`}</p>
        </div>
        <button className="btn-icone" aria-label="Fechar o painel do passo" onClick={onFechar}><Icon name="x" /></button>
      </div>

      <div role="tablist" aria-label="Seções do passo" className="abas abas-painel"
        onKeyDown={(e) => {
          const abas: Aba[] = linha ? ['parametros', 'configuracoes', 'execucao'] : ['parametros', 'configuracoes']
          const i = abas.indexOf(aba)
          const novo = e.key === 'ArrowRight' ? abas[(i + 1) % abas.length] : e.key === 'ArrowLeft' ? abas[(i - 1 + abas.length) % abas.length] : null
          if (novo) { e.preventDefault(); setAba(novo); document.getElementById(`aba-passo-${novo}`)?.focus() }
        }}>
        {([['parametros', 'Parâmetros'], ['configuracoes', 'Configurações'], ['execucao', 'Execução']] as [Aba, string][])
          .filter(([id]) => id !== 'execucao' || !!linha).filter(([id]) => id !== 'configuracoes' || !gatilho).map(([id, rotulo]) => (
            <button key={id} role="tab" id={`aba-passo-${id}`} aria-selected={aba === id} aria-controls={`painel-passo-${id}`}
              tabIndex={aba === id ? 0 : -1} className="aba" onClick={() => setAba(id)}>
              {rotulo}{id === 'parametros' && meus.some((i) => i.severity === 'erro') && <span className="contagem" aria-label="há erros">{meus.filter((i) => i.severity === 'erro').length}</span>}
            </button>
          ))}
      </div>

      <div className="painel-corpo" role="tabpanel" id={`painel-passo-${aba}`} aria-labelledby={`aba-passo-${aba}`}>
        {aba === 'parametros' && (
          <>
            <p className="descricao-bloco">{def.description}</p>
            {ultimaVersao && ultimaVersao > passo.version && (
              <Aviso tipo="aviso" titulo={`Existe a v${ultimaVersao} deste bloco`}>
                Este fluxo continua usando a v{passo.version} e não muda sozinho.
                <div className="acoes-linha"><button className="btn btn-pequeno" onClick={() => onAtualizarVersao(passo)}>Atualizar para v{ultimaVersao}</button></div>
              </Aviso>
            )}
            {bloqueado && <Aviso tipo="aviso" titulo="O executor isolado está indisponível">{sistema?.executor.mensagem} {sistema?.executor.instrucao}</Aviso>}
            {referenciasQuebradas > 0 && (
              <Aviso tipo="erro" titulo="Conteúdo dinâmico removido">
                {referenciasQuebradas === 1 ? 'Um campo usa' : `${referenciasQuebradas} campos usam`} o conteúdo de um passo que não existe mais. Escolha outro conteúdo ou apague o campo.
              </Aviso>
            )}
            {meus.filter((i) => !i.field).map((i, k) => <Aviso key={k} tipo={i.severity === 'erro' ? 'erro' : 'aviso'}>{semPrefixo(i.message, nome)}{i.hint && <div className="campo-ajuda">{i.hint}</div>}</Aviso>)}
            {def.id === 'builtin.escopo' && (
              <Aviso tipo="info" titulo="Como tratar erros">
                Coloque os passos que podem falhar aqui dentro. Depois do escopo, adicione um passo e, em <strong>Configurações › Executar após</strong>, marque
                “falhou”: ele roda só quando algo dentro do escopo falhar. O conteúdo “Mensagem do erro” fica disponível para ele.
              </Aviso>
            )}
            {campos}
            {def.id === 'builtin.python' && !somenteLeitura && (
              <div className="acoes-linha">
                <button type="button" className="btn btn-pequeno" onClick={() => onSalvarComoBloco(passo)}><Icon name="plus" size={14} /> Salvar como bloco reutilizável</button>
              </div>
            )}
            {def.kind === 'python' && !somenteLeitura && (
              <div className="acoes-linha"><button type="button" className="btn btn-pequeno" onClick={() => onEditarBloco(passo)}><Icon name="edit" size={14} /> Editar o código do bloco</button></div>
            )}
          </>
        )}

        {aba === 'configuracoes' && !gatilho && (
          <>
            <fieldset className="campo" disabled={somenteLeitura || indice === 0}>
              <legend className="rotulo-campo">Executar após</legend>
              {indice === 0 ? (
                <p className="campo-ajuda">O primeiro passo de uma lista sempre roda quando o bloco que o contém roda.</p>
              ) : (
                <>
                  <p className="campo-ajuda">Este passo roda quando o passo anterior…</p>
                  {(Object.keys(ROTULO_EXECUTAR_APOS) as ExecutarApos[]).map((r) => {
                    const atual = passo.run_after ?? ['sucesso']
                    return (
                      <label key={r} className="checagem">
                        <input type="checkbox" checked={atual.includes(r)}
                          onChange={(e) => {
                            const novo = e.target.checked ? [...atual, r] : atual.filter((x) => x !== r)
                            if (novo.length === 0) return  // sempre ao menos uma situação
                            atualizar((x) => ({ ...x, run_after: novo }), `executar-apos:${passo.id}`)
                          }} />
                        {ROTULO_EXECUTAR_APOS[r]}
                      </label>
                    )
                  })}
                  <p className="campo-ajuda">Dica: para tratar a falha de vários passos, coloque-os dentro de um <strong>Escopo</strong> e configure o passo seguinte para rodar após “falhou”.</p>
                </>
              )}
            </fieldset>

            <fieldset className="campo" disabled={somenteLeitura}>
              <legend className="rotulo-campo">Política de repetição</legend>
              <div className="grade-2">
                <div>
                  <label htmlFor="retry-n">Novas tentativas</label>
                  <input id="retry-n" type="number" min={0} max={5} value={passo.settings?.retry.count ?? 0}
                    onChange={(e) => atualizar((x) => ({ ...x, settings: { retry: { count: Math.max(0, Math.min(5, Number(e.target.value) || 0)), interval_s: x.settings?.retry.interval_s ?? 2 }, timeout_s: x.settings?.timeout_s ?? null } }), `retry:${passo.id}`)} />
                </div>
                <div>
                  <label htmlFor="retry-i">Intervalo (s)</label>
                  <input id="retry-i" type="number" min={0} max={30} step="any" value={passo.settings?.retry.interval_s ?? 2}
                    onChange={(e) => atualizar((x) => ({ ...x, settings: { retry: { count: x.settings?.retry.count ?? 0, interval_s: Math.max(0, Math.min(30, Number(e.target.value) || 0)) }, timeout_s: x.settings?.timeout_s ?? null } }), `retry:${passo.id}`)} />
                </div>
              </div>
              <p className="campo-ajuda">Se o passo falhar, ele é tentado de novo. Erros de configuração (campo vazio, tipo errado) não são repetidos.</p>
            </fieldset>

            {codigoAtivo && (
              <div className="campo">
                <label htmlFor="timeout-s">Tempo limite do código (s)</label>
                <input id="timeout-s" type="number" min={1} max={sistema?.limits.time_s ?? 3600} step="any" disabled={somenteLeitura}
                  value={passo.settings?.timeout_s ?? ''} placeholder={sistema ? `${sistema.limits.time_s} (padrão do servidor)` : 'padrão do servidor'}
                  onChange={(e) => atualizar((x) => ({ ...x, settings: { retry: x.settings?.retry ?? { count: 0, interval_s: 2 }, timeout_s: e.target.value === '' ? null : Number(e.target.value) } }), `timeout:${passo.id}`)} />
                <p className="campo-ajuda">Pode ser menor, mas não maior, que o limite do servidor{sistema ? ` (${sistema.limits.time_s} s)` : ''}.</p>
              </div>
            )}

            <div className="campo">
              <label htmlFor="anotacao"><Icon name="note" size={14} /> Anotação</label>
              <textarea id="anotacao" rows={3} maxLength={500} value={passo.note ?? ''} disabled={somenteLeitura} placeholder="Explique o que este passo faz, para quem for ler o fluxo depois."
                onChange={(e) => atualizar((x) => ({ ...x, note: e.target.value || null }), `nota:${passo.id}`)} />
            </div>
          </>
        )}

        {aba === 'execucao' && linha && (
          <>
            <div className="resumo-execucao">
              <EstadoBadge estado={linha.state} />
              {linha.duration_ms != null && linha.state !== 'ignorado' && <span>Duração: {formatarDuracao(linha.duration_ms)}</span>}
              {iteracaoTexto && <span>{iteracaoTexto}</span>}
            </div>
            {linha.skip_reason && <Aviso tipo="info" titulo="Não executado">{linha.skip_reason}</Aviso>}
            {linha.error && (
              <Aviso tipo="erro" titulo={linha.error.technical?.line ? `Falhou na linha ${linha.error.technical.line}` : 'Falhou'}>
                {linha.error.message}
                {linha.error.suggestion && <div className="campo-ajuda"><strong>O que fazer:</strong> {linha.error.suggestion}</div>}
              </Aviso>
            )}
            <DetalheTecnico passo={linha} />
            <h3 className="secao">Entradas</h3>
            <pre className="valor">{linha.inputs && Object.keys(linha.inputs).length ? formatarValor(linha.inputs) : '—'}</pre>
            <h3 className="secao">Saídas</h3>
            <pre className="valor">{linha.outputs ? formatarValor(linha.outputs) : '—'}</pre>
            {linha.logs.length > 0 && (
              <>
                <h3 className="secao">Logs</h3>
                {linha.logs.map((l, k) => <pre key={k} className={`log log-${classeOrigem(l.source)}`}><span className="log-origem">{rotuloOrigem(l.source)}</span>{l.text}</pre>)}
              </>
            )}
          </>
        )}
      </div>
    </aside>
  )
}
