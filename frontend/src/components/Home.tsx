import { useEffect, useRef, useState } from 'react'
import { api, ApiFailure } from '../api'
import { formatarDataRelativa } from '../lib/visual'
import type { BlockType, ProjectSummary, SystemInfo } from '../types'
import { BlockEditorDialog } from './BlockEditorDialog'
import { Icon, Logo } from './Icons'
import { Aviso, Confirmar, Dialog, EstadoBadge, useNotificar } from './ui'

function baixar(nome: string, conteudo: string) {
  const url = URL.createObjectURL(new Blob([conteudo], { type: 'application/json' }))
  const a = document.createElement('a')
  a.href = url; a.download = nome; a.click(); URL.revokeObjectURL(url)
}

export function Home({ onAbrir }: { onAbrir: (id: string) => void }) {
  const notificar = useNotificar()
  const [projetos, setProjetos] = useState<ProjectSummary[] | null>(null)
  const [sistema, setSistema] = useState<SystemInfo | null>(null)
  const [erro, setErro] = useState<ApiFailure | null>(null)
  const [novo, setNovo] = useState(false)
  const [nomeNovo, setNomeNovo] = useState('')
  const [excluindo, setExcluindo] = useState<ProjectSummary | null>(null)
  const [blocos, setBlocos] = useState<BlockType[] | null>(null)
  const [editandoBloco, setEditandoBloco] = useState<{ b?: BlockType } | null>(null)
  const arquivo = useRef<HTMLInputElement>(null)

  async function carregar() {
    try {
      const [p, s] = await Promise.all([api.projetos(), api.sistema()])
      setProjetos(p); setSistema(s); setErro(null)
    } catch (e) { setErro(e as ApiFailure) }
  }
  useEffect(() => { void carregar() }, [])

  async function criar() {
    const nome = nomeNovo.trim()
    if (!nome) return
    try {
      const p = await api.criarProjeto(nome)
      onAbrir(p.id)
    } catch (e) { notificar.erro('Não foi possível criar o projeto', (e as ApiFailure).message) }
  }

  async function importar(f: File | undefined) {
    if (!f) return
    try {
      const conteudo = JSON.parse(await f.text())
      const p = await api.importarProjeto(conteudo)
      notificar.sucesso(`“${p.name}” importado`, [...p.warnings, p.created_blocks.length ? `Blocos adicionados à biblioteca: ${p.created_blocks.join(', ')}.` : ''].filter(Boolean).join(' '))
      onAbrir(p.id)
    } catch (e) {
      if (e instanceof SyntaxError) notificar.erro('Esse arquivo não é um JSON válido', 'Escolha um arquivo exportado pela Trama (.trama.json).')
      else {
        const x = e as ApiFailure
        notificar.erro(x.message, x.issues.slice(0, 3).map((i) => i.message).join(' ') || x.suggestion || undefined)
      }
    } finally {
      if (arquivo.current) arquivo.current.value = ''
    }
  }

  async function exportar(p: ProjectSummary) {
    try {
      const cheio = await api.projeto(p.id)
      const dados = await api.exportarFluxo(cheio.name, cheio.description, cheio.flow)
      baixar(`${cheio.name.replace(/[^\p{L}\p{N}]+/gu, '-').toLowerCase() || 'fluxo'}.trama.json`, JSON.stringify(dados, null, 2))
    } catch (e) { notificar.erro('Não foi possível exportar', (e as ApiFailure).message) }
  }

  async function excluir() {
    if (!excluindo) return
    try {
      await api.excluirProjeto(excluindo.id)
      notificar.sucesso(`Projeto “${excluindo.name}” excluído`)
      setExcluindo(null)
      await carregar()
    } catch (e) { notificar.erro('Não foi possível excluir', (e as ApiFailure).message) }
  }

  async function abrirBlocos() {
    try { setBlocos((await api.blocos()).filter((b) => b.kind === 'python')) }
    catch (e) { notificar.erro('Não foi possível carregar os blocos', (e as ApiFailure).message) }
  }

  const executorOk = sistema?.executor.disponivel ?? false

  return (
    <div className="home">
      <header className="home-topo" role="banner">
        <span className="barra-marca grande"><Logo size={40} /><span className="marca-nome">Trama</span></span>
        <p className="lema">Programação visual em Python: conecte blocos, escreva código quando precisar.</p>
      </header>
      <main className="home-corpo" id="conteudo">
        {sistema && !sistema.executor.disponivel && (
          <Aviso tipo="aviso" titulo="O executor isolado de Python não está disponível">
            {sistema.executor.mensagem} {sistema.executor.instrucao} Enquanto isso, você pode montar e executar fluxos com os blocos
            internos; os blocos com código Python ficam desabilitados.
          </Aviso>
        )}
        {erro && <Aviso tipo="erro" titulo={erro.message}>{erro.suggestion}</Aviso>}

        <div className="home-titulo">
          <h1>Seus projetos</h1>
          <div className="acoes-linha">
            <button className="btn" onClick={() => void abrirBlocos()}><Icon name="python" size={16} /> Blocos Python</button>
            <button className="btn" onClick={() => arquivo.current?.click()}><Icon name="upload" size={16} /> Importar fluxo</button>
            <input ref={arquivo} type="file" accept=".json,application/json" className="sr-only" tabIndex={-1} aria-label="Escolher arquivo de fluxo para importar"
              onChange={(e) => void importar(e.target.files?.[0])} />
            <button className="btn btn-primario" onClick={() => { setNomeNovo(''); setNovo(true) }}><Icon name="plus" size={16} /> Novo projeto</button>
          </div>
        </div>

        {projetos === null && !erro && <p role="status">Carregando projetos…</p>}
        {projetos?.length === 0 && (
          <div className="vazio-grande">
            <Icon name="plus" size={32} />
            <p>Você ainda não tem projetos. Crie o primeiro ou importe um fluxo.</p>
          </div>
        )}
        <ul className="cartoes">
          {projetos?.map((p) => (
            <li key={p.id} className="cartao">
              <h2><button className="link-cartao" onClick={() => onAbrir(p.id)}>{p.name}</button></h2>
              <p className="cartao-desc">{p.description || 'Sem descrição.'}</p>
              <p className="cartao-meta">
                {p.block_count} {p.block_count === 1 ? 'bloco' : 'blocos'} · atualizado {formatarDataRelativa(p.updated_at)}
              </p>
              {p.last_run_state && <p className="cartao-meta">Última execução: <EstadoBadge estado={p.last_run_state} compacto /></p>}
              <div className="cartao-acoes">
                <button className="btn btn-primario-suave btn-pequeno" onClick={() => onAbrir(p.id)}>Abrir</button>
                <button className="btn btn-pequeno" onClick={() => void exportar(p)}><Icon name="download" size={14} /> Exportar</button>
                <button className="btn btn-pequeno btn-perigo-suave" onClick={() => setExcluindo(p)} aria-label={`Excluir o projeto ${p.name}`}><Icon name="trash" size={14} /> Excluir</button>
              </div>
            </li>
          ))}
        </ul>
      </main>

      {novo && (
        <Dialog titulo="Novo projeto" onClose={() => setNovo(false)} largura={460}
          rodape={<>
            <button className="btn" onClick={() => setNovo(false)}>Cancelar</button>
            <button className="btn btn-primario" onClick={() => void criar()} disabled={!nomeNovo.trim()}>Criar e abrir</button>
          </>}>
          <div className="campo">
            <label htmlFor="novo-nome">Nome do projeto</label>
            <input id="novo-nome" value={nomeNovo} maxLength={120} data-autofocus placeholder="Ex.: Calcular desconto"
              onChange={(e) => setNomeNovo(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') void criar() }} />
          </div>
        </Dialog>
      )}
      {excluindo && (
        <Confirmar titulo="Excluir projeto?" rotuloConfirmar="Excluir projeto" perigo
          mensagem={<>O projeto <strong>{excluindo.name}</strong> e o histórico de execuções dele serão apagados. Isso não pode ser desfeito.</>}
          onConfirmar={() => void excluir()} onCancelar={() => setExcluindo(null)} />
      )}
      {blocos && (
        <Dialog titulo="Blocos Python da biblioteca" onClose={() => setBlocos(null)} largura={640}
          descricao="Blocos criados por você. Cada edição cria uma nova versão; os fluxos continuam na versão que fixaram."
          rodape={<>
            <button className="btn" onClick={() => setBlocos(null)}>Fechar</button>
            <button className="btn btn-primario" onClick={() => setEditandoBloco({})}><Icon name="plus" size={16} /> Novo bloco Python</button>
          </>}>
          {blocos.length === 0 ? <p className="vazio">Nenhum bloco Python ainda.</p> : (
            <ul className="lista-blocos">
              {blocos.map((b) => (
                <li key={b.id}>
                  <div><strong>{b.name}</strong> <span className="versao-chip">v{b.version}</span><p className="campo-ajuda">{b.description || 'Sem descrição.'}</p></div>
                  <button className="btn btn-pequeno" onClick={() => setEditandoBloco({ b })}><Icon name="edit" size={14} /> Editar</button>
                </li>
              ))}
            </ul>
          )}
        </Dialog>
      )}
      {editandoBloco && (
        <BlockEditorDialog
          editar={editandoBloco.b} baseVersao={editandoBloco.b?.version} categorias={['Personalizados']} executorOk={executorOk}
          onClose={() => setEditandoBloco(null)}
          onSalvo={(b, avisos) => { setEditandoBloco(null); notificar.sucesso(`Bloco “${b.name}” salvo (v${b.version})`, avisos[0]); void abrirBlocos() }}
          onExcluido={() => { notificar.sucesso('Bloco excluído'); void abrirBlocos() }}
        />
      )}
    </div>
  )
}
