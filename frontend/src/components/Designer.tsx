import { createContext, useContext, useMemo, type ReactNode } from 'react'
import {
  CONTEINERES_DE_LACO, chaveIteracao, descreverCampo, definicaoEfetiva, executarAposPadrao, iteracaoEscolhida, linhaDoPasso, nomeDoPasso,
  ROTULO_EXECUTAR_APOS, todosOsPassos, type Destino,
} from '../lib/modelo'
import { corDaCategoria, formatarDuracao, iconeDoBloco } from '../lib/visual'
import type { BlockType, Flow, Issue, Passo, Ref, Regra, Run } from '../types'
import { Icon } from './Icons'
import { EstadoBadge, MenuDeAcoes } from './ui'

export type AcaoDoPasso = 'duplicar' | 'excluir' | 'subir' | 'descer'

export interface DesignerProps {
  flow: Flow
  defDe: (p: Passo) => BlockType | undefined
  selecionadoId: string | null
  destinoAtivo: Destino | null
  onSelecionar: (id: string) => void
  onAdicionar: (d: Destino) => void
  onAcao: (id: string, a: AcaoDoPasso) => void
  problemas: Issue[]
  run: Run | null
  somenteLeitura: boolean
  iteracoes: Record<string, number>
  onIteracao: (chave: string, n: number) => void
  /** Última versão de cada bloco Python da biblioteca (para avisar que existe uma mais nova). */
  ultimasVersoes: Map<string, number>
}

type Contexto = DesignerProps & { porId: Map<string, Passo> }
const Ctx = createContext<Contexto | null>(null)
function useDesigner(): Contexto {
  const c = useContext(Ctx)
  if (!c) throw new Error('Designer fora do contexto')
  return c
}

const mesmoDestino = (a: Destino | null, b: Destino) => !!a && a.paiId === b.paiId && a.espaco === b.espaco && a.indice === b.indice

// ------------------------------------------------------------------ resumo do que o passo faz
function resumoDoPasso(p: Passo, def: BlockType | undefined, nomeDaRef: (r: Ref) => string): string {
  if (!def) return ''
  const campo = (id: string) => descreverCampo(p.inputs[id], nomeDaRef)
  const opcao = (paramId: string) => {
    const pd = def.params.find((x) => x.id === paramId)
    const v = p.params[paramId] ?? pd?.default
    return pd?.options.find((o) => o.value === v)?.label ?? ''
  }
  switch (def.id) {
    case 'builtin.compor': return campo('entrada')
    case 'builtin.condicao': case 'builtin.repetir_ate': {
      const regras = (Array.isArray(p.params.regras) ? p.params.regras : []) as Regra[]
      const ops = def.params.find((x) => x.id === 'regras')?.options ?? []
      const texto = regras.map((r) => `${descreverCampo(r.esq, nomeDaRef) || '…'} ${ops.find((o) => o.value === r.op)?.label ?? r.op}${r.dir ? ` ${descreverCampo(r.dir, nomeDaRef) || '…'}` : ''}`)
      return texto.join(p.params.combinador === 'ou' ? ' ou ' : ' e ')
    }
    case 'builtin.para_cada': return `Percorrer ${campo('lista') || 'uma lista'}`
    case 'builtin.matematica': return `${campo('a') || '…'} ${opcao('operacao').split(' (')[0].toLowerCase()} ${campo('b') || '…'}`.trim()
    case 'builtin.texto': return opcao('operacao')
    case 'builtin.transformar_lista': return opcao('operacao')
    case 'builtin.var_inicializar': return String(p.params.nome ?? '')
    case 'builtin.encerrar': return opcao('estado')
    case 'builtin.saida': return String(p.params.titulo ?? '')
    case 'builtin.python': {
      const ef = definicaoEfetiva(def, p.params)
      return `${ef.inputs.map((i) => i.id).join(', ') || 'sem entradas'} → ${ef.outputs.map((o) => o.id).join(', ') || '…'}`
    }
    case 'builtin.gatilho_manual': {
      const ef = definicaoEfetiva(def, p.params)
      return ef.outputs.length ? `Campos: ${ef.outputs.map((o) => o.label).join(', ')}` : 'Quando você clicar em Testar'
    }
    default: return ''
  }
}

// ------------------------------------------------------------------ conectores com “+”
function Conector({ destino, rotulo, final }: { destino: Destino; rotulo: string; final?: boolean }) {
  const c = useDesigner()
  const ativo = mesmoDestino(c.destinoAtivo, destino)
  return (
    <div className={`conector${final ? ' conector-final' : ''}`}>
      <span className="conector-linha" aria-hidden="true" />
      {!c.somenteLeitura && (
        final && destino.paiId === null ? (
          <button type="button" className={`btn novo-passo${ativo ? ' ativo' : ''}`} aria-label={`Novo passo. ${rotulo}`} onClick={() => c.onAdicionar(destino)}>
            <Icon name="plus" size={16} /> Novo passo
          </button>
        ) : (
          <button type="button" className={`conector-mais${ativo ? ' ativo' : ''}`} aria-label={rotulo} title={rotulo} onClick={() => c.onAdicionar(destino)}>
            <Icon name="plus" size={14} />
          </button>
        )
      )}
      {!final && <span className="conector-seta" aria-hidden="true" />}
    </div>
  )
}

// ------------------------------------------------------------------ sequência de passos
function Sequencia({
  passos, paiId, espaco, contexto, rotulo, dica,
}: { passos: Passo[]; paiId: string | null; espaco: string | null; contexto: number[]; rotulo: string; dica: string }) {
  const c = useDesigner()
  if (passos.length === 0 && paiId === null) {
    return (
      <div className="sequencia-principal-vazia">
        <Conector final destino={{ paiId: null, espaco: null, indice: 0 }} rotulo="Adicionar o primeiro passo do fluxo" />
        <p className="campo-ajuda">{dica}</p>
      </div>
    )
  }
  if (passos.length === 0) {
    return (
      <div className="sequencia-vazia">
        <p>{dica}</p>
        {!c.somenteLeitura && (
          <button type="button" className={`btn btn-pequeno${mesmoDestino(c.destinoAtivo, { paiId, espaco, indice: 0 }) ? ' ativo' : ''}`}
            aria-label={`Adicionar um passo em “${rotulo}”`} onClick={() => c.onAdicionar({ paiId, espaco, indice: 0 })}>
            <Icon name="plus" size={14} /> Adicionar um passo
          </button>
        )}
      </div>
    )
  }
  const nomeAntes = (i: number) => (i === 0 ? (paiId ? `no início de “${rotulo}”` : 'logo depois do gatilho')
    : `depois de “${nomeDoPasso(passos[i - 1], c.defDe(passos[i - 1]))}”`)
  return (
    <ol className="sequencia" aria-label={rotulo}>
      {passos.map((p, i) => (
        <li key={p.id} className="item-sequencia">
          <Conector destino={{ paiId, espaco, indice: i }} rotulo={`Adicionar um passo ${nomeAntes(i)}`} />
          <PassoNaSequencia passo={p} indice={i} irmaos={passos} contexto={contexto} />
        </li>
      ))}
      <li className="item-sequencia item-final">
        <Conector final destino={{ paiId, espaco, indice: passos.length }}
          rotulo={`Adicionar um passo ${paiId ? `no fim de “${rotulo}”` : 'no fim do fluxo'}`} />
      </li>
    </ol>
  )
}

// ------------------------------------------------------------------ o cartão de um passo
function CartaoBase({
  passo, indice, irmaos, contexto, gatilho,
}: { passo: Passo; indice: number; irmaos: Passo[]; contexto: number[]; gatilho?: boolean }) {
  const c = useDesigner()
  const def = c.defDe(passo)
  const linha = linhaDoPasso(c.run, passo.id, contexto)
  const nome = nomeDoPasso(passo, def)
  const nomeDaRef = (r: Ref) => {
    const alvo = c.porId.get(r.step)
    const d = alvo ? c.defDe(alvo) : undefined
    const ef = alvo && d ? definicaoEfetiva(d, alvo.params) : undefined
    return ef?.outputs.find((o) => o.id === r.output)?.label ?? r.output
  }
  const meus = c.problemas.filter((i) => i.step_id === passo.id)
  const erros = meus.filter((i) => i.severity === 'erro').length
  const avisos = meus.length - erros
  const cor = corDaCategoria(def?.category ?? 'Personalizados')
  const selecionado = c.selecionadoId === passo.id
  const resumo = resumoDoPasso(passo, def, nomeDaRef)
  const estado = linha?.state ?? (c.run && !c.somenteLeitura && c.run.state !== 'concluido' && c.run.state !== 'falhou' && c.run.state !== 'cancelado' ? 'aguardando' : undefined)
  const ultimo = indice === irmaos.length - 1
  const itens = [
    { rotulo: 'Duplicar', icone: 'copy', onClick: () => c.onAcao(passo.id, 'duplicar'), desabilitado: c.somenteLeitura },
    { rotulo: 'Mover para cima', icone: 'arrowUp', onClick: () => c.onAcao(passo.id, 'subir'), desabilitado: c.somenteLeitura || indice === 0 },
    { rotulo: 'Mover para baixo', icone: 'arrowDown', onClick: () => c.onAcao(passo.id, 'descer'), desabilitado: c.somenteLeitura || ultimo },
    { rotulo: 'Excluir', icone: 'trash', perigo: true, onClick: () => c.onAcao(passo.id, 'excluir'), desabilitado: c.somenteLeitura },
  ]
  const tentativas = passo.settings?.retry.count ?? 0

  if (!def) {
    return (
      <div className="cartao-passo cartao-desconhecido" role="group" aria-label={`Bloco indisponível: ${nome}`}>
        <button type="button" className="cartao-principal" onClick={() => c.onSelecionar(passo.id)} aria-pressed={selecionado}>
          <span className="cartao-icone"><Icon name="alert" size={18} /></span>
          <span className="cartao-textos">
            <span className="cartao-nome">{nome}</span>
            <span className="cartao-resumo">Este bloco (v{passo.version}) não está disponível nesta instalação.</span>
          </span>
        </button>
        {!c.somenteLeitura && <MenuDeAcoes rotulo={`Mais ações para ${nome}`} itens={itens} />}
      </div>
    )
  }

  return (
    <div
      className={`cartao-passo${selecionado ? ' selecionado' : ''}${estado ? ` estado-${estado}` : ''}${gatilho ? ' cartao-gatilho' : ''}`}
      style={{ ['--cor' as string]: cor }} role="group" aria-label={`${gatilho ? 'Gatilho' : 'Passo'} ${nome}${estado ? `, ${estado}` : ''}`}
      data-passo={passo.id}
    >
      <button type="button" className="cartao-principal" onClick={() => c.onSelecionar(passo.id)} aria-pressed={selecionado}
        aria-describedby={`${passo.id}-detalhes`}>
        <span className="cartao-icone"><Icon name={iconeDoBloco(def.id, def.kind)} size={18} /></span>
        <span className="cartao-textos">
          <span className="cartao-nome">{nome}</span>
          {passo.label && <span className="cartao-tipo">{def.name}</span>}
          {resumo && <span className="cartao-resumo">{resumo}</span>}
        </span>
      </button>
      <span className="cartao-lateral">
        {estado && (
          <span className="cartao-estado">
            <EstadoBadge estado={estado} compacto />
            {linha?.duration_ms != null && estado !== 'ignorado' && estado !== 'executando' && <span className="cartao-duracao">{formatarDuracao(linha.duration_ms)}</span>}
          </span>
        )}
        {!c.run && erros > 0 && <span className="etiqueta etiqueta-erro" title="Há erros a corrigir neste passo"><Icon name="alert" size={12} /> {erros}<span className="sr-only"> {erros === 1 ? 'erro' : 'erros'}</span></span>}
        {!c.run && erros === 0 && avisos > 0 && <span className="etiqueta etiqueta-aviso" title="Há avisos neste passo"><Icon name="info" size={12} /> {avisos}<span className="sr-only"> {avisos === 1 ? 'aviso' : 'avisos'}</span></span>}
        {!gatilho && !c.somenteLeitura && <MenuDeAcoes rotulo={`Mais ações para ${nome}`} itens={itens} />}
      </span>
      <span id={`${passo.id}-detalhes`} className="cartao-etiquetas">
        {!gatilho && indice > 0 && !executarAposPadrao(passo) && (
          <span className="etiqueta" title="Este passo só roda em certas situações do passo anterior">
            Executar após: {(passo.run_after ?? []).map((r) => ROTULO_EXECUTAR_APOS[r]).join(' ou ')}
          </span>
        )}
        {def.kind === 'python' && (
          <span className="etiqueta" title="Versão do bloco fixada neste fluxo">
            v{passo.version}{(c.ultimasVersoes.get(def.id) ?? 0) > passo.version && ` · existe a v${c.ultimasVersoes.get(def.id)}`}
          </span>
        )}
        {tentativas > 0 && <span className="etiqueta"><Icon name="retry" size={12} /> {tentativas} {tentativas === 1 ? 'nova tentativa' : 'novas tentativas'}</span>}
        {passo.note && <span className="etiqueta" title={passo.note}><Icon name="note" size={12} /> Anotação</span>}
        {estado === 'ignorado' && linha?.skip_reason && <span className="cartao-motivo">{linha.skip_reason}</span>}
        {linha?.error && estado === 'falhou' && <span className="cartao-motivo cartao-motivo-erro">{linha.error.message}</span>}
      </span>
    </div>
  )
}

function PassoNaSequencia({ passo, indice, irmaos, contexto }: { passo: Passo; indice: number; irmaos: Passo[]; contexto: number[] }) {
  const c = useDesigner()
  const def = c.defDe(passo)
  if (!def || def.slots.length === 0) return <CartaoBase passo={passo} indice={indice} irmaos={irmaos} contexto={contexto} />

  const laco = CONTEINERES_DE_LACO.has(def.id)
  const { total, atual } = laco ? iteracaoEscolhida(c.run, passo, contexto, c.iteracoes) : { total: 0, atual: 0 }
  const chave = chaveIteracao(passo.id, contexto)
  const contextoFilhos = laco ? [...contexto, atual] : contexto
  const nome = nomeDoPasso(passo, def)

  const corpo = (slotId: string, rotulo: string, dica: string): ReactNode => (
    <Sequencia passos={passo.slots?.[slotId] ?? []} paiId={passo.id} espaco={slotId} contexto={contextoFilhos} rotulo={`${rotulo} — ${nome}`} dica={dica} />
  )

  return (
    <div className={`conteiner conteiner-${def.id === 'builtin.condicao' ? 'condicao' : laco ? 'laco' : 'escopo'}`} style={{ ['--cor' as string]: corDaCategoria(def.category) }}>
      <CartaoBase passo={passo} indice={indice} irmaos={irmaos} contexto={contexto} />
      {laco && total > 0 && (
        <div className="iteracoes" role="group" aria-label={`Repetições de ${nome}`}>
          <button type="button" className="btn-icone" aria-label="Repetição anterior" disabled={atual === 0} onClick={() => c.onIteracao(chave, atual - 1)}><Icon name="chevronDown" className="girar-90" size={16} /></button>
          <span role="status">Repetição {atual + 1} de {total}</span>
          <button type="button" className="btn-icone" aria-label="Próxima repetição" disabled={atual >= total - 1} onClick={() => c.onIteracao(chave, atual + 1)}><Icon name="chevronRight" size={16} /></button>
        </div>
      )}
      {def.id === 'builtin.condicao' ? (
        <div className="ramos">
          {def.slots.map((s) => {
            const filhos = passo.slots?.[s.id] ?? []
            const ignorado = !!c.run && filhos.length > 0 && filhos.every((f) => linhaDoPasso(c.run, f.id, contextoFilhos)?.state === 'ignorado')
            return (
              <section key={s.id} className={`ramo ramo-${s.id}${ignorado ? ' ramo-ignorado' : ''}`} aria-label={`${s.label} — ${nome}`}>
                <h3><Icon name={s.id === 'sim' ? 'check' : 'x'} size={14} /> {s.label}{ignorado && <span className="ramo-nota"> (não escolhido)</span>}</h3>
                {corpo(s.id, s.label, s.empty_hint)}
              </section>
            )
          })}
        </div>
      ) : (
        <div className="corpo-conteiner">
          {def.slots.map((s) => <div key={s.id}>{corpo(s.id, s.label, s.empty_hint)}</div>)}
        </div>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ o designer
export function Designer(props: DesignerProps) {
  const { flow } = props
  const porId = useMemo(() => new Map(todosOsPassos(flow).map((p) => [p.id, p])), [flow])
  return (
    <Ctx.Provider value={{ ...props, porId }}>
      <div className="designer" aria-label="Fluxo">
        <CartaoBase passo={flow.trigger} indice={0} irmaos={[flow.trigger]} contexto={[]} gatilho />
        <Sequencia passos={flow.steps} paiId={null} espaco={null} contexto={[]} rotulo="Passos do fluxo" dica="Este fluxo ainda não tem passos." />
      </div>
    </Ctx.Provider>
  )
}
