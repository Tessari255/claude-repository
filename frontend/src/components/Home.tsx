import { useEffect, useRef, useState } from 'react'
import { api, ApiFailure } from '../api'
import { formatarDataRelativa } from '../lib/visual'
import type { BlockType, Modelo, ProjectSummary, SystemInfo } from '../types'
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
  const [modelos, setModelos] = useState<Modelo[]>([])
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
    api.modelos().then(setModelos).catch(() => undefined)
  }
  useEffect(() => { void carregar() }, [])

  async function criar() {
    const nome = nomeNovo.trim()
    if (!nome) return
    try {
      const p = await api.criarProjeto(nome)
      onAbrir(p.id)
    } catch (e) { notificar.erro('Não foi possível criar o fluxo', (e as ApiFailure).message) }
  }

  async function usarModelo(m: Modelo) {
    try {
      const p = await api.importarProjeto(m.file)
      onAbrir(p.id)
    } catch (e) { notificar.erro('Não foi possível usar o modelo', (e as ApiFailure).message) }
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
      notificar.sucesso(`Fluxo “${excluindo.name}” excluído`)
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
        <p className="lema">Automatize com fluxos de passos e escreva Python quando precisar.</p>
      </header>
      <main className="home-corpo" id="conteudo">
        {sistema && !sistema.executor.disponivel && (
          <Aviso tipo="aviso" titulo="O executor isolado de Python não está disponível">
            {sistema.executor.mensagem} {sistema.executor.instrucao} Enquanto isso, você pode montar e testar fluxos com os blocos
            internos; os passos com código Python ficam desabilitados.
          </Aviso>
        )}
        {erro && <Aviso tipo="erro" titulo={erro.message}>{erro.suggestion}</Aviso>}

        <div className="home-titulo">
          <h1>Meus fluxos</h1>
          <div className="acoes-linha">
            <button className="btn" onClick={() => void abrirBlocos()}><Icon name="python" size={16} /> Blocos Python</button>
            <button className="btn" onClick={() => arquivo.current?.click()}><Icon name="upload" size={16} /> Importar fluxo</button>
            <input ref={arquivo} type="file" accept=".json,application/json" className="sr-only" tabIndex={-1} aria-label="Escolher arquivo de fluxo para importar"
              onChange={(e) => void importar(e.target.files?.[0])} />
            <button className="btn btn-primario" onClick={() => { setNomeNovo(''); setNovo(true) }}><Icon name="plus" size={16} /> Novo fluxo</button>
          </div>
        </div>

        {projetos === null && !erro && <p role="status">Carregando fluxos…</p>}
        {projetos?.length === 0 && (
          <div className="vazio-grande">
            <Icon name="plus" size={32} />
            <p>Você ainda não tem fluxos. Crie o primeiro, importe um arquivo ou comece por um modelo logo abaixo.</p>
          </div>
        )}
        {projetos && projetos.length > 0 && (
          <table className="tabela tabela-fluxos">
            <caption className="sr-only">Seus fluxos</caption>
            <thead>
              <tr><th scope="col">Nome</th><th scope="col">Passos</th><th scope="col">Modificado</th><th scope="col">Última execução</th><th scope="col"><span className="sr-only">Ações</span></th></tr>
            </thead>
            <tbody>
              {projetos.map((p) => (
                <tr key={p.id}>
                  <th scope="row" className="fluxo-nome">
                    <button className="link-cartao" onClick={() => onAbrir(p.id)}>{p.name}</button>
                    {p.description && <span className="cartao-desc">{p.description}</span>}
                  </th>
                  <td>{p.step_count}</td>
                  <td>{formatarDataRelativa(p.updated_at)}</td>
                  <td>{p.last_run_state ? <><EstadoBadge estado={p.last_run_state} compacto /> <span className="campo-ajuda">{p.last_run_at ? formatarDataRelativa(p.last_run_at) : ''}</span></> : <span className="campo-ajuda">Nunca executado</span>}</td>
                  <td className="acoes-tabela">
                    <button className="btn btn-primario-suave btn-pequeno" onClick={() => onAbrir(p.id)} aria-label={`Abrir o fluxo ${p.name}`}>Abrir</button>
                    <button className="btn btn-pequeno" onClick={() => void exportar(p)} aria-label={`Exportar o fluxo ${p.name}`}><Icon name="download" size={14} /> Exportar</button>
                    <button className="btn btn-pequeno btn-perigo-suave" onClick={() => setExcluindo(p)} aria-label={`Excluir o fluxo ${p.name}`}><Icon name="trash" size={14} /> Excluir</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {modelos.length > 0 && (
          <section aria-labelledby="titulo-modelos" className="secao-modelos">
            <div className="home-titulo"><h2 id="titulo-modelos"><Icon name="template" size={20} /> Comece por um modelo</h2></div>
            <ul className="cartoes">
              {modelos.map((m) => (
                <li key={m.id} className="cartao">
                  <h3>{m.name}</h3>
                  <p className="cartao-desc">{m.description}</p>
                  <p className="cartao-meta">{m.step_count} {m.step_count === 1 ? 'passo' : 'passos'}</p>
                  <div className="cartao-acoes">
                    <button className="btn btn-primario-suave btn-pequeno" onClick={() => void usarModelo(m)} aria-label={`Usar o modelo ${m.name}`}>Usar este modelo</button>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}
      </main>

      {novo && (
        <Dialog titulo="Novo fluxo" onClose={() => setNovo(false)} largura={460}
          rodape={<>
            <button className="btn" onClick={() => setNovo(false)}>Cancelar</button>
            <button className="btn btn-primario" onClick={() => void criar()} disabled={!nomeNovo.trim()}>Criar e abrir</button>
          </>}>
          <div className="campo">
            <label htmlFor="novo-nome">Nome do fluxo</label>
            <input id="novo-nome" value={nomeNovo} maxLength={120} data-autofocus placeholder="Ex.: Calcular desconto"
              onChange={(e) => setNomeNovo(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') void criar() }} />
          </div>
        </Dialog>
      )}
      {excluindo && (
        <Confirmar titulo="Excluir fluxo?" rotuloConfirmar="Excluir fluxo" perigo
          mensagem={<>O fluxo <strong>{excluindo.name}</strong> e o histórico de execuções dele serão apagados. Isso não pode ser desfeito.</>}
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
