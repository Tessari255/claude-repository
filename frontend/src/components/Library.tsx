import { useMemo, useState } from 'react'
import type { BlockType } from '../types'
import { corDaCategoria } from '../lib/visual'
import { iconeDoBloco } from './BlockNode'
import { Icon } from './Icons'

export const MIME_BLOCO = 'application/x-trama-bloco'

const ORDEM = ['Entrada e saída', 'Dados', 'Cálculo', 'Texto', 'Controle de fluxo', 'Personalizados']

export function Library({
  blocos, executorOk, onAdicionar, onNovoBlocoPython, onEditarBloco,
}: {
  blocos: BlockType[]
  executorOk: boolean
  onAdicionar: (b: BlockType) => void
  onNovoBlocoPython: () => void
  onEditarBloco: (b: BlockType) => void
}) {
  const [busca, setBusca] = useState('')
  const grupos = useMemo(() => {
    const q = busca.trim().toLowerCase()
    const filtrados = blocos.filter((b) =>
      !q || `${b.name} ${b.description} ${b.category}`.toLowerCase().includes(q))
    const mapa = new Map<string, BlockType[]>()
    for (const b of filtrados) mapa.set(b.category, [...(mapa.get(b.category) ?? []), b])
    return [...mapa.entries()].sort(([a], [b]) => {
      const ia = ORDEM.indexOf(a), ib = ORDEM.indexOf(b)
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b, 'pt-BR')
    })
  }, [blocos, busca])

  return (
    <aside className="painel painel-biblioteca" aria-label="Biblioteca de blocos">
      <div className="painel-topo">
        <h2>Blocos</h2>
        <button className="btn btn-pequeno" onClick={onNovoBlocoPython} title="Criar um bloco com código Python">
          <Icon name="plus" size={14} /> Bloco Python
        </button>
      </div>
      <div className="busca">
        <Icon name="search" size={16} />
        <input
          type="search" aria-label="Buscar blocos" placeholder="Buscar blocos…" value={busca}
          onChange={(e) => setBusca(e.target.value)}
          id="busca-blocos"
        />
      </div>
      <p className="dica-biblioteca">Arraste para a área de trabalho, ou use Enter para adicionar o bloco ao centro.</p>
      <div className="biblioteca-lista">
        {grupos.length === 0 && <p className="vazio">Nenhum bloco encontrado para “{busca}”.</p>}
        {grupos.map(([categoria, itens]) => (
          <section key={categoria} aria-label={categoria} className="grupo">
            <h3 style={{ ['--cor' as string]: corDaCategoria(categoria) }}>{categoria}</h3>
            {categoria === 'Controle de fluxo' && (
              <p className="grupo-nota">
                <Icon name="info" size={14} /> Limitação desta versão: caminhos diferentes de uma condição
                não podem ser reunidos em um mesmo bloco.
              </p>
            )}
            <ul>
              {itens.map((b) => {
                const bloqueado = b.kind === 'python' && !executorOk
                return (
                  <li key={`${b.id}@${b.version}`} className="item-bloco">
                    <button
                      className="item-bloco-botao" draggable
                      style={{ ['--cor' as string]: corDaCategoria(b.category) }}
                      onDragStart={(e) => {
                        e.dataTransfer.setData(MIME_BLOCO, JSON.stringify({ id: b.id, version: b.version }))
                        e.dataTransfer.effectAllowed = 'copy'
                      }}
                      onClick={() => onAdicionar(b)}
                      aria-label={`Adicionar bloco ${b.name}. ${b.description}`}
                      title={b.description}
                    >
                      <span className="item-icone"><Icon name={iconeDoBloco(b.id, b.kind)} size={16} /></span>
                      <span className="item-texto">
                        <span className="item-nome">{b.name}{b.kind === 'python' && <span className="item-versao"> v{b.version}</span>}</span>
                        <span className="item-desc">{b.description}</span>
                        {bloqueado && <span className="item-bloqueio"><Icon name="lock" size={12} /> Precisa do executor isolado</span>}
                      </span>
                    </button>
                    {b.kind === 'python' && (
                      <button className="btn-icone" aria-label={`Editar o bloco ${b.name}`} title="Editar (cria uma nova versão)"
                        onClick={() => onEditarBloco(b)}><Icon name="edit" size={16} /></button>
                    )}
                  </li>
                )
              })}
            </ul>
          </section>
        ))}
      </div>
    </aside>
  )
}
