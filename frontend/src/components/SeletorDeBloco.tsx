import { useMemo, useState } from 'react'
import { corDaCategoria, iconeDoBloco, ORDEM_CATEGORIAS } from '../lib/visual'
import type { BlockType } from '../types'
import { Icon } from './Icons'

/** Painel “Adicionar um passo”: busca e categorias, como o seletor de ações do Power Automate. */
export function SeletorDeBloco({
  blocos, executorOk, contexto, profundidadeMaxima, onEscolher, onNovoBlocoPython, onEditarBloco, onFechar,
}: {
  blocos: BlockType[]
  executorOk: boolean
  contexto: string
  profundidadeMaxima: boolean
  onEscolher: (b: BlockType) => void
  onNovoBlocoPython: () => void
  onEditarBloco: (b: BlockType) => void
  onFechar: () => void
}) {
  const [busca, setBusca] = useState('')
  const [categoria, setCategoria] = useState<string>('Todos')

  const disponiveis = useMemo(() => blocos.filter((b) => !b.trigger), [blocos])
  const categorias = useMemo(() => {
    const presentes = new Set(disponiveis.map((b) => b.category))
    const ordenadas = ORDEM_CATEGORIAS.filter((c) => presentes.has(c))
    const outras = [...presentes].filter((c) => !ORDEM_CATEGORIAS.includes(c)).sort((a, b) => a.localeCompare(b, 'pt-BR'))
    return ['Todos', ...ordenadas, ...outras]
  }, [disponiveis])

  const grupos = useMemo(() => {
    const q = busca.trim().toLowerCase()
    const filtrados = disponiveis.filter((b) =>
      (categoria === 'Todos' || b.category === categoria) && (!q || `${b.name} ${b.description} ${b.category}`.toLowerCase().includes(q)))
    const mapa = new Map<string, BlockType[]>()
    for (const b of filtrados) mapa.set(b.category, [...(mapa.get(b.category) ?? []), b])
    return [...mapa.entries()].sort(([a], [b]) => {
      const ia = ORDEM_CATEGORIAS.indexOf(a), ib = ORDEM_CATEGORIAS.indexOf(b)
      return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b, 'pt-BR')
    })
  }, [disponiveis, busca, categoria])

  return (
    <aside className="painel-direito" aria-label="Adicionar um passo">
      <div className="painel-topo">
        <h2>Adicionar um passo</h2>
        <button className="btn-icone" aria-label="Fechar" onClick={onFechar}><Icon name="x" /></button>
      </div>
      <p className="painel-contexto">{contexto}</p>
      <div className="busca">
        <Icon name="search" size={16} />
        <input type="search" aria-label="Buscar blocos" placeholder="Buscar blocos…" value={busca} id="busca-blocos" data-autofocus autoFocus
          onChange={(e) => setBusca(e.target.value)} />
      </div>
      <div className="filtros-categoria" role="group" aria-label="Filtrar por categoria">
        {categorias.map((c) => (
          <button key={c} type="button" className="filtro" aria-pressed={categoria === c} onClick={() => setCategoria(c)}>{c}</button>
        ))}
      </div>
      <div className="painel-corpo lista-blocos-seletor">
        {grupos.length === 0 && <p className="vazio">Nenhum bloco encontrado para “{busca}”.</p>}
        {grupos.map(([cat, itens]) => (
          <section key={cat} aria-label={cat} className="grupo">
            <h3 style={{ ['--cor' as string]: corDaCategoria(cat) }}>{cat}</h3>
            <ul>
              {itens.map((b) => {
                const bloqueado = (b.kind === 'python' || b.id === 'builtin.python') && !executorOk
                const semEspaco = profundidadeMaxima && b.slots.length > 0
                return (
                  <li key={`${b.id}@${b.version}`} className="item-bloco">
                    <button
                      className="item-bloco-botao" disabled={semEspaco} style={{ ['--cor' as string]: corDaCategoria(b.category) }}
                      onClick={() => onEscolher(b)} aria-label={`Adicionar ${b.name}. ${b.description}`}
                    >
                      <span className="item-icone"><Icon name={iconeDoBloco(b.id, b.kind)} size={16} /></span>
                      <span className="item-texto">
                        <span className="item-nome">{b.name}{b.kind === 'python' && <span className="item-versao"> v{b.version}</span>}</span>
                        <span className="item-desc">{b.description}</span>
                        {bloqueado && <span className="item-bloqueio"><Icon name="lock" size={12} /> Precisa do executor isolado</span>}
                        {semEspaco && <span className="item-bloqueio"><Icon name="info" size={12} /> Aninhamento máximo atingido</span>}
                      </span>
                    </button>
                    {b.kind === 'python' && (
                      <button className="btn-icone" aria-label={`Editar o bloco ${b.name}`} title="Editar (cria uma nova versão)" onClick={() => onEditarBloco(b)}>
                        <Icon name="edit" size={16} />
                      </button>
                    )}
                  </li>
                )
              })}
            </ul>
          </section>
        ))}
      </div>
      <div className="painel-rodape">
        <button className="btn" onClick={onNovoBlocoPython}><Icon name="plus" size={16} /> Criar bloco Python reutilizável</button>
      </div>
    </aside>
  )
}
