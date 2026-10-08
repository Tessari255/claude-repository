import { Handle, Position, type NodeProps } from '@xyflow/react'
import { memo } from 'react'
import type { BlockNode as BlockNodeTipo } from '../lib/flow'
import { corDaCategoria, FORMA_TIPO, ROTULO_ESTADO, ROTULO_TIPO, formatarDuracao } from '../lib/visual'
import type { PortDef, TipoDado } from '../types'
import { Icon } from './Icons'
import { EstadoBadge } from './ui'

export const ICONE_BLOCO: Record<string, string> = {
  'builtin.inicio': 'play', 'builtin.saida': 'flag', 'builtin.constante': 'value', 'builtin.matematica': 'calc',
  'builtin.texto': 'text', 'builtin.selecionar_campos': 'pick', 'builtin.condicao': 'branch', 'builtin.para_cada': 'loop',
}

export function iconeDoBloco(id: string, kind?: string): string {
  return ICONE_BLOCO[id] ?? (kind === 'python' || id.startsWith('custom.') ? 'python' : 'value')
}

function Porta({ porta, lado, tipo }: { porta: PortDef; lado: 'entrada' | 'saida'; tipo: TipoDado }) {
  return (
    <li className={`porta-linha porta-${lado}`} title={porta.description || undefined}>
      <Handle
        id={porta.id}
        type={lado === 'entrada' ? 'target' : 'source'}
        position={lado === 'entrada' ? Position.Left : Position.Right}
        className={`porta forma-${FORMA_TIPO[tipo]} tipo-${tipo}`}
        role="img"
        aria-label={`${lado === 'entrada' ? 'Entrada' : 'Saída'} ${porta.label}, tipo ${ROTULO_TIPO[tipo]}`}
      />
      <span className="porta-rotulo">
        {porta.conditional && <Icon name="branch" size={12} />}
        {porta.label}
        {lado === 'entrada' && porta.required && <span className="obrigatorio" aria-label="obrigatória"> *</span>}
      </span>
      <span className="porta-tipo">{ROTULO_TIPO[tipo]}</span>
    </li>
  )
}

function BlockNodeView({ data, selected }: NodeProps<BlockNodeTipo>) {
  const def = data.def
  const estado = data.step?.state
  const cor = corDaCategoria(def?.category ?? 'Personalizados')
  const nome = data.block.label || def?.name || data.block.type
  const desatualizado = data.latestVersion && data.latestVersion > data.block.version

  if (!def) {
    return (
      <div className="no no-desconhecido" tabIndex={-1}>
        <header className="no-topo"><Icon name="alert" /><span className="no-nome">{nome}</span></header>
        <p className="no-aviso">Este bloco (versão {data.block.version}) não está disponível nesta instalação.</p>
      </div>
    )
  }

  return (
    <div
      className={`no ${selected ? 'no-selecionado' : ''} ${estado ? `no-estado-${estado}` : ''}`}
      style={{ ['--cor' as string]: cor }}
    >
      <header className="no-topo">
        <span className="no-icone"><Icon name={iconeDoBloco(def.id, def.kind)} size={16} /></span>
        <span className="no-nome">{nome}</span>
        <span className="no-versao" title={desatualizado ? `Existe a v${data.latestVersion}. Este fluxo usa a v${data.block.version}.` : `Versão ${data.block.version}`}>
          v{data.block.version}{desatualizado ? ' ↑' : ''}
        </span>
      </header>
      {(estado || data.problemCount > 0) && (
        <div className="no-status">
          {estado && <EstadoBadge estado={estado} compacto />}
          {estado && data.step?.duration_ms != null && estado !== 'executando' && estado !== 'ignorado' && (
            <span className="no-duracao">{formatarDuracao(data.step.duration_ms)}</span>
          )}
          {data.problemCount > 0 && !estado && (
            <span className="problema-chip" title="Este bloco tem problemas a corrigir">
              <Icon name="alert" size={12} /> {data.problemCount} {data.problemCount === 1 ? 'problema' : 'problemas'}
            </span>
          )}
        </div>
      )}
      {estado === 'ignorado' && data.step?.skip_reason && <p className="no-motivo">{data.step.skip_reason}</p>}
      <div className="no-portas">
        <ul className="no-lista no-entradas" aria-label="Entradas">
          {def.inputs.map((p) => <Porta key={p.id} porta={p} lado="entrada" tipo={p.type} />)}
        </ul>
        <ul className="no-lista no-saidas" aria-label="Saídas">
          {def.outputs.map((p) => <Porta key={p.id} porta={p} lado="saida" tipo={data.portTypes?.outputs[p.id] ?? p.type} />)}
        </ul>
      </div>
    </div>
  )
}

export const BlockNodeComponent = memo(BlockNodeView)

export function descricaoDoNo(nome: string, estado: string | undefined, problemas: number): string {
  const partes = [`Bloco ${nome}`]
  if (estado) partes.push(`estado: ${ROTULO_ESTADO[estado as keyof typeof ROTULO_ESTADO]}`)
  if (problemas > 0) partes.push(`${problemas} ${problemas === 1 ? 'problema' : 'problemas'} a corrigir`)
  return partes.join(', ')
}
