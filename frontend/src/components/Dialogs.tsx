import { Dialog } from './ui'

const ATALHOS: [string, string][] = [
  ['Ctrl + S', 'Salvar o fluxo'],
  ['Ctrl + Enter', 'Abrir o painel de teste (e testar, se não houver nada a preencher)'],
  ['Ctrl + Z', 'Desfazer a última alteração'],
  ['Ctrl + Y ou Ctrl + Shift + Z', 'Refazer'],
  ['Tab / Shift + Tab', 'Navegar entre passos, botões “+” e campos'],
  ['Enter ou Espaço (em um passo)', 'Abrir a configuração do passo'],
  ['Enter ou Espaço (em um “+”)', 'Escolher um novo passo para aquele ponto'],
  ['⋯ (menu do passo)', 'Duplicar, mover para cima ou para baixo e excluir'],
  ['Esc', 'Fechar o painel aberto, o menu ou o seletor de conteúdo dinâmico'],
  ['Botão “Conteúdo dinâmico”', 'Inserir no campo o resultado de um passo anterior'],
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
