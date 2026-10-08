import { useEffect, useState } from 'react'
import { api, ApiFailure } from '../api'
import { valorPadraoDoTipo, type BlockNode } from '../lib/flow'
import type { Run } from '../types'
import { ResultadoDoTeste } from './TestResult'
import { Aviso, Dialog, useNotificar } from './ui'
import { ValueField } from './ValueField'

export function TestarBlocoDialog({ no, projectId, onClose }: { no: BlockNode; projectId: string; onClose: () => void }) {
  const def = no.data.def!
  const notificar = useNotificar()
  const [inputs, setInputs] = useState<Record<string, unknown>>({})
  const [usar, setUsar] = useState<Record<string, boolean>>({})
  const [origem, setOrigem] = useState<'exemplo' | 'execucao'>('exemplo')
  const [run, setRun] = useState<Run | null>(null)
  const [erro, setErro] = useState<ApiFailure | null>(null)
  const [rodando, setRodando] = useState(false)

  useEffect(() => {
    let vivo = true
    ;(async () => {
      const anteriores = no.data.step?.inputs
      let base: Record<string, unknown> = {}
      try { base = (await api.exemploDoBloco(def.id, def.version)).inputs } catch { /* usa padrões do tipo */ }
      if (!vivo) return
      if (anteriores && Object.keys(anteriores).length) { base = { ...base, ...anteriores }; setOrigem('execucao') }
      const valores: Record<string, unknown> = {}
      const marcados: Record<string, boolean> = {}
      for (const p of def.inputs) {
        valores[p.id] = p.id in base ? base[p.id] : valorPadraoDoTipo(p.type === 'qualquer' ? 'texto' : p.type)
        marcados[p.id] = p.required || p.id in base
      }
      setInputs(valores)
      setUsar(marcados)
    })()
    return () => { vivo = false }
  }, [def, no.data.step?.inputs])

  async function testar() {
    setRodando(true); setErro(null); setRun(null)
    try {
      const enviados = Object.fromEntries(def.inputs.filter((p) => usar[p.id]).map((p) => [p.id, inputs[p.id]]))
      const r = await api.testarBloco({ ref: { type: def.id, version: def.version }, params: no.data.block.params, inputs: enviados, project_id: projectId })
      setRun(r)
      notificar.anunciar(r.state === 'concluido' ? 'Teste concluído.' : `Teste falhou: ${r.error?.message ?? ''}`)
    } catch (e) {
      setErro(e as ApiFailure)
    } finally {
      setRodando(false)
    }
  }

  return (
    <Dialog titulo={`Testar “${no.data.block.label || def.name}”`} onClose={onClose} largura={640}
      descricao="Executa somente este bloco, com os dados de exemplo abaixo e a configuração atual dele."
      rodape={<>
        <button className="btn" onClick={onClose}>Fechar</button>
        <button className="btn btn-primario" onClick={testar} disabled={rodando} data-autofocus>{rodando ? 'Testando…' : 'Testar agora'}</button>
      </>}>
      {def.inputs.length === 0 && <p className="campo-ajuda">Este bloco não tem entradas: ele usa só a configuração.</p>}
      {origem === 'execucao' && <p className="campo-ajuda">Os valores foram preenchidos com o que o bloco recebeu na última execução.</p>}
      {def.inputs.map((p) => (
        <div key={p.id} className="campo-teste">
          {!p.required && (
            <label className="checagem">
              <input type="checkbox" checked={!!usar[p.id]} onChange={(e) => setUsar((u) => ({ ...u, [p.id]: e.target.checked }))} />
              Informar “{p.label}” (opcional)
            </label>
          )}
          {(p.required || usar[p.id]) && (
            <ValueField tipo={p.type} rotulo={p.label} obrigatorio={p.required} valor={inputs[p.id]}
              onChange={(v) => setInputs((i) => ({ ...i, [p.id]: v }))} />
          )}
        </div>
      ))}
      {erro && (
        <Aviso tipo="erro" titulo={erro.message}>
          {erro.issues.map((i, k) => <p key={k}>{i.message}</p>)}
          {erro.suggestion && <p>{erro.suggestion}</p>}
        </Aviso>
      )}
      {run && <ResultadoDoTeste run={run} />}
    </Dialog>
  )
}

export function ExecutarComDadosDialog({
  inicios, onExecutar, onClose,
}: {
  inicios: BlockNode[]
  onExecutar: (dados: Record<string, Record<string, unknown>>) => void
  onClose: () => void
}) {
  const [dados, setDados] = useState<Record<string, Record<string, unknown>>>(() =>
    Object.fromEntries(inicios.map((n) => [n.id, (n.data.block.params.dados as Record<string, unknown>) ?? {}])))
  return (
    <Dialog titulo="Executar com dados" onClose={onClose} largura={620}
      descricao="Informe os dados desta execução. Eles substituem, só desta vez, os dados configurados em cada bloco “Início manual”."
      rodape={<>
        <button className="btn" onClick={onClose}>Cancelar</button>
        <button className="btn btn-primario" onClick={() => onExecutar(dados)} data-autofocus>Executar</button>
      </>}>
      {inicios.length === 0 && <Aviso tipo="info">Este fluxo não tem um bloco “Início manual”. Adicione um para fornecer dados.</Aviso>}
      {inicios.map((n) => (
        <ValueField key={n.id} tipo="json" rotulo={`Dados de “${n.data.block.label || n.data.def?.name}”`}
          valor={dados[n.id]} onChange={(v) => setDados((d) => ({ ...d, [n.id]: v as Record<string, unknown> }))}
          ajuda='Objeto JSON. Exemplo: {"nome": "Ana"}' />
      ))}
    </Dialog>
  )
}

const ATALHOS: [string, string][] = [
  ['Ctrl + S', 'Salvar o projeto'],
  ['Ctrl + Enter', 'Executar o fluxo'],
  ['Ctrl + D', 'Duplicar os blocos selecionados'],
  ['Delete ou Backspace', 'Excluir os blocos ou conexões selecionados'],
  ['Ctrl + A', 'Selecionar todos os blocos (com o foco na área de trabalho)'],
  ['Esc', 'Limpar a seleção'],
  ['Tab / Shift + Tab', 'Navegar entre blocos e controles'],
  ['Enter ou Espaço (em um bloco)', 'Selecionar o bloco'],
  ['Setas (com um bloco selecionado)', 'Mover o bloco'],
  ['Shift + arrastar', 'Selecionar vários blocos com um retângulo'],
  ['Roda do mouse', 'Aumentar ou diminuir o zoom'],
  ['Arrastar o fundo', 'Mover a área de trabalho'],
  ['Painel “Entradas”', 'Conectar blocos pelo teclado, sem arrastar'],
  ['/', 'Buscar na biblioteca de blocos'],
  ['Editor de código: Esc, depois Tab', 'Sair do editor de código com o teclado (ou Ctrl + M)'],
]

export function AtalhosDialog({ onClose }: { onClose: () => void }) {
  return (
    <Dialog titulo="Atalhos de teclado" onClose={onClose} largura={560}
      rodape={<button className="btn btn-primario" onClick={onClose} data-autofocus>Fechar</button>}>
      <table className="tabela">
        <caption className="sr-only">Lista de atalhos de teclado</caption>
        <thead><tr><th scope="col">Atalho</th><th scope="col">O que faz</th></tr></thead>
        <tbody>{ATALHOS.map(([a, b]) => <tr key={a}><td><kbd>{a}</kbd></td><td>{b}</td></tr>)}</tbody>
      </table>
    </Dialog>
  )
}
