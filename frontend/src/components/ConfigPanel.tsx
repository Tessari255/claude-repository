import {
  parametroVisivel, tipoEfetivoDoParametro, valorDoParametro, type BlockNode, type FlowEdge,
} from '../lib/flow'
import { ROTULO_TIPO, formatarDuracao, formatarValor } from '../lib/visual'
import type { Issue, SystemInfo, TipoDado } from '../types'
import { iconeDoBloco } from './BlockNode'
import { Icon } from './Icons'
import { Aviso, Detalhes, EstadoBadge, TipoChip } from './ui'
import { ValueField } from './ValueField'

const compativeis = (origem: string, destino: string) => origem === destino || origem === 'qualquer' || destino === 'qualquer'

function semPrefixo(msg: string, nome: string) {
  return msg.startsWith(`${nome}: `) ? msg.slice(nome.length + 2) : msg
}

export interface AcoesConfig {
  onParam: (blockId: string, paramId: string, valor: unknown) => void
  onRotulo: (blockId: string, rotulo: string) => void
  onConectar: (origem: { block: string; port: string }, destino: { block: string; port: string }) => void
  onDesconectar: (destino: { block: string; port: string }) => void
  onTestar: (blockId: string) => void
  onDuplicar: () => void
  onExcluir: () => void
  onAtualizarVersao: (blockId: string) => void
  onEditarBloco: (blockId: string) => void
  onVerDetalhes: () => void
  onIrParaBloco: (blockId: string) => void
}

export function ConfigPanel({
  selecionados, todos, arestas, problemas, sistema, acoes, nomeProjeto, descricaoProjeto, onNomeProjeto, onDescricaoProjeto,
}: {
  selecionados: BlockNode[]
  todos: BlockNode[]
  arestas: FlowEdge[]
  problemas: Issue[]
  sistema: SystemInfo | null
  acoes: AcoesConfig
  nomeProjeto: string
  descricaoProjeto: string
  onNomeProjeto: (v: string) => void
  onDescricaoProjeto: (v: string) => void
}) {
  if (selecionados.length === 0) {
    return (
      <aside className="painel painel-config" aria-label="Configuração">
        <div className="painel-topo"><h2>Projeto</h2></div>
        <div className="painel-corpo">
          <div className="campo">
            <label htmlFor="proj-nome">Nome do projeto</label>
            <input id="proj-nome" value={nomeProjeto} onChange={(e) => onNomeProjeto(e.target.value)} maxLength={120} />
          </div>
          <div className="campo">
            <label htmlFor="proj-desc">Descrição</label>
            <textarea id="proj-desc" rows={3} value={descricaoProjeto} onChange={(e) => onDescricaoProjeto(e.target.value)} maxLength={1000} />
          </div>
          <p className="campo-ajuda">{todos.length} {todos.length === 1 ? 'bloco' : 'blocos'} · {arestas.length} {arestas.length === 1 ? 'conexão' : 'conexões'}</p>
          <Aviso tipo="info" titulo="Como montar um fluxo">
            <ol className="passos">
              <li>Arraste blocos da biblioteca para a área de trabalho.</li>
              <li>Ligue uma <strong>saída</strong> (à direita do bloco) a uma <strong>entrada</strong> (à esquerda). Cores, formas e nomes mostram o tipo de cada porta.</li>
              <li>Selecione um bloco para configurá-lo aqui.</li>
              <li>Clique em <strong>Executar</strong> e veja o resultado embaixo.</li>
            </ol>
          </Aviso>
          <Aviso tipo="info" titulo="Condições: uma limitação desta versão">
            Os dois caminhos de uma condição (“se verdadeiro” e “se falso”) levam a blocos diferentes e <strong>não podem ser
            reunidos em um mesmo bloco</strong>. Para continuar depois de uma condição, repita o bloco em cada caminho.
          </Aviso>
          {sistema && !sistema.executor.disponivel && (
            <Aviso tipo="aviso" titulo="Código Python desabilitado">
              {sistema.executor.mensagem} {sistema.executor.instrucao}
            </Aviso>
          )}
          {sistema && sistema.executor.disponivel && (
            <p className="campo-ajuda">
              Código Python roda em contêiner isolado, sem rede, com limite de {sistema.limits.time_s} s,
              {' '}{sistema.limits.memory_mb} MB de memória e {sistema.limits.logs_kb} KB de saída.
            </p>
          )}
        </div>
      </aside>
    )
  }

  if (selecionados.length > 1) {
    return (
      <aside className="painel painel-config" aria-label="Configuração">
        <div className="painel-topo"><h2>{selecionados.length} blocos selecionados</h2></div>
        <div className="painel-corpo">
          <p className="campo-ajuda">Selecione um único bloco para editar a configuração dele.</p>
          <div className="acoes-linha">
            <button className="btn" onClick={acoes.onDuplicar}><Icon name="copy" size={16} /> Duplicar</button>
            <button className="btn btn-perigo-suave" onClick={acoes.onExcluir}><Icon name="trash" size={16} /> Excluir</button>
          </div>
        </div>
      </aside>
    )
  }

  const no = selecionados[0]
  const def = no.data.def
  const bloco = no.data.block
  const nome = bloco.label || def?.name || bloco.type
  const meusProblemas = problemas.filter((p) => p.block_id === no.id)
  const step = no.data.step
  const desatualizado = no.data.latestVersion && no.data.latestVersion > bloco.version
  const bloqueado = def?.kind === 'python' && sistema && !sistema.executor.disponivel

  const opcoesOrigem = (() => {
    const lista: { valor: string; rotulo: string; tipo: string; bloco: string; porta: string }[] = []
    for (const outro of todos) {
      if (outro.id === no.id || !outro.data.def) continue
      for (const s of outro.data.def.outputs) {
        const tipo = outro.data.portTypes?.outputs[s.id] ?? s.type
        lista.push({
          valor: `${outro.id}::${s.id}`, tipo, bloco: outro.id, porta: s.id,
          rotulo: `${outro.data.block.label || outro.data.def.name} › ${s.label} (${ROTULO_TIPO[tipo as TipoDado] ?? tipo})`,
        })
      }
    }
    return lista
  })()

  if (!def) {
    return (
      <aside className="painel painel-config" aria-label="Configuração">
        <div className="painel-topo"><h2>Bloco indisponível</h2></div>
        <div className="painel-corpo">
          <Aviso tipo="erro" titulo={`${bloco.type} (v${bloco.version})`}>
            Este bloco não existe nesta instalação. Você ainda pode excluí-lo do fluxo.
          </Aviso>
          <button className="btn btn-perigo-suave" onClick={acoes.onExcluir}><Icon name="trash" size={16} /> Excluir bloco</button>
        </div>
      </aside>
    )
  }

  return (
    <aside className="painel painel-config" aria-label={`Configuração do bloco ${nome}`}>
      <div className="painel-topo">
        <h2><Icon name={iconeDoBloco(def.id, def.kind)} size={18} /> {def.name}</h2>
        <span className="versao-chip" title="Versão fixada neste fluxo">v{bloco.version}</span>
      </div>
      <div className="painel-corpo">
        <p className="descricao-bloco">{def.description}</p>

        {desatualizado && (
          <Aviso tipo="aviso" titulo={`Existe a v${no.data.latestVersion} deste bloco`}>
            Este fluxo continua usando a v{bloco.version} e não muda sozinho.
            <div className="acoes-linha">
              <button className="btn btn-pequeno" onClick={() => acoes.onAtualizarVersao(no.id)}>
                Atualizar para v{no.data.latestVersion}
              </button>
            </div>
          </Aviso>
        )}

        {def.id === 'builtin.condicao' && (
          <Aviso tipo="info" titulo="Dois caminhos, uma só direção">
            Só o caminho escolhido é executado; os blocos do outro caminho aparecem como “ignorados”.
            Limitação: os dois caminhos <strong>não podem ser reunidos</strong> em um mesmo bloco.
          </Aviso>
        )}

        <div className="campo">
          <label htmlFor="bloco-rotulo">Nome neste fluxo</label>
          <input id="bloco-rotulo" value={bloco.label ?? ''} placeholder={def.name} maxLength={80}
            onChange={(e) => acoes.onRotulo(no.id, e.target.value)} />
        </div>

        {def.params.some((p) => parametroVisivel(def, bloco.params, p)) && <h3 className="secao">Configuração</h3>}
        {def.params.filter((p) => parametroVisivel(def, bloco.params, p)).map((p) => {
          const tipo = p.type === 'selecao' || p.type === 'codigo' ? p.type : tipoEfetivoDoParametro(def, bloco.params, p)
          const erro = meusProblemas.find((i) => i.param === p.id)
          return (
            <ValueField
              key={`${no.id}-${p.id}-${tipo}`} // recria o campo quando o tipo dinâmico muda
              tipo={tipo} rotulo={p.label} valor={valorDoParametro(def, bloco.params, p)} opcoes={p.options}
              multilinha={p.multiline} placeholder={p.placeholder} ajuda={p.help} obrigatorio={p.required}
              erro={erro ? semPrefixo(erro.message, nome) : null}
              onChange={(v) => acoes.onParam(no.id, p.id, v)}
            />
          )
        })}

        {def.inputs.length > 0 && <h3 className="secao">Entradas</h3>}
        {def.inputs.map((porta) => {
          const aresta = arestas.find((e) => e.target === no.id && e.targetHandle === porta.id)
          const atual = aresta ? `${aresta.source}::${aresta.sourceHandle}` : ''
          const idSel = `entrada-${no.id}-${porta.id}`
          const erro = meusProblemas.find((i) => i.port === porta.id && i.code === 'entrada_obrigatoria')
          return (
            <div className="campo" key={porta.id}>
              <div className="rotulo-linha">
                <label htmlFor={idSel}>
                  {porta.label}{porta.required && <span className="obrigatorio" aria-hidden="true"> *</span>}
                </label>
                <span id={`${idSel}-tipo`}><TipoChip tipo={porta.type as TipoDado} /></span>
              </div>
              <select id={idSel} value={atual} aria-invalid={erro ? true : undefined} aria-required={porta.required || undefined}
                aria-describedby={`${idSel}-tipo`}
                onChange={(e) => {
                  if (!e.target.value) return acoes.onDesconectar({ block: no.id, port: porta.id })
                  const op = opcoesOrigem.find((o) => o.valor === e.target.value)!
                  acoes.onConectar({ block: op.bloco, port: op.porta }, { block: no.id, port: porta.id })
                }}>
                <option value="">— Sem conexão —</option>
                {opcoesOrigem.map((o) => (
                  <option key={o.valor} value={o.valor} disabled={!compativeis(o.tipo, porta.type)}>
                    {o.rotulo}{compativeis(o.tipo, porta.type) ? '' : ' — tipo incompatível'}
                  </option>
                ))}
              </select>
              {porta.description && <p className="campo-ajuda">{porta.description}</p>}
              {erro && <p className="campo-erro">{semPrefixo(erro.message, nome)}</p>}
            </div>
          )
        })}

        {def.outputs.length > 0 && (
          <>
            <h3 className="secao">Saídas</h3>
            <ul className="lista-saidas">
              {def.outputs.map((s) => (
                <li key={s.id}>
                  <span>{s.conditional && <Icon name="branch" size={12} />} {s.label}</span>
                  <TipoChip tipo={(no.data.portTypes?.outputs[s.id] ?? s.type) as TipoDado} />
                </li>
              ))}
            </ul>
          </>
        )}

        {meusProblemas.filter((p) => !p.param && p.code !== 'entrada_obrigatoria').map((p, i) => (
          <Aviso key={i} tipo={p.severity === 'erro' ? 'erro' : 'aviso'}>{semPrefixo(p.message, nome)}{p.hint && <div className="campo-ajuda">{p.hint}</div>}</Aviso>
        ))}

        {step && (
          <>
            <h3 className="secao">Última execução</h3>
            <div className="ultima-exec">
              <EstadoBadge estado={step.state} />
              {step.duration_ms != null && step.state !== 'ignorado' && <span className="campo-ajuda"> {formatarDuracao(step.duration_ms)}</span>}
            </div>
            {step.skip_reason && <p className="campo-ajuda">{step.skip_reason}</p>}
            {step.error && (
              <Aviso tipo="erro" titulo="Falhou">
                {step.error.message}
                <div className="acoes-linha"><button className="btn btn-pequeno" onClick={acoes.onVerDetalhes}>Ver detalhes do erro</button></div>
              </Aviso>
            )}
            {step.inputs && Object.keys(step.inputs).length > 0 && (
              <Detalhes resumo="Entradas recebidas"><pre className="valor">{formatarValor(step.inputs)}</pre></Detalhes>
            )}
            {step.outputs && (
              <Detalhes resumo="Saídas produzidas"><pre className="valor">{formatarValor(step.outputs)}</pre></Detalhes>
            )}
          </>
        )}

        <h3 className="secao">Ações</h3>
        <div className="acoes-linha acoes-quebra">
          <button className="btn btn-primario-suave" onClick={() => acoes.onTestar(no.id)} disabled={!!bloqueado}
            title={bloqueado ? 'O executor isolado está indisponível' : 'Executar só este bloco com dados de exemplo'}>
            <Icon name="play" size={16} /> Testar este bloco
          </button>
          {def.kind === 'python' && (
            <button className="btn" onClick={() => acoes.onEditarBloco(no.id)}><Icon name="edit" size={16} /> Editar bloco</button>
          )}
          <button className="btn" onClick={acoes.onDuplicar}><Icon name="copy" size={16} /> Duplicar</button>
          <button className="btn btn-perigo-suave" onClick={acoes.onExcluir}><Icon name="trash" size={16} /> Excluir</button>
        </div>
      </div>
    </aside>
  )
}
