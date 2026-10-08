import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Aviso } from './ui'

/** Contém falhas de renderização (ex.: dados inesperados vindos do servidor) sem derrubar a tela inteira. */
export class ErrorBoundary extends Component<
  { children: ReactNode; titulo?: string; resetKey?: unknown; acao?: ReactNode },
  { falhou: boolean }
> {
  state = { falhou: false }

  static getDerivedStateFromError() {
    return { falhou: true }
  }

  componentDidCatch(erro: Error, info: ErrorInfo) {
    console.error('Falha ao exibir uma parte da tela:', erro, info.componentStack)
  }

  componentDidUpdate(anterior: { resetKey?: unknown }) {
    // quando o conteúdo exibido muda (ex.: outra execução), tenta renderizar de novo
    if (this.state.falhou && anterior.resetKey !== this.props.resetKey) this.setState({ falhou: false })
  }

  render() {
    if (!this.state.falhou) return this.props.children
    return (
      <div className="erro-contido">
        <Aviso tipo="erro" titulo={this.props.titulo ?? 'Não foi possível exibir esta parte da tela'}>
          Os dados recebidos tinham um formato inesperado. O restante do editor continua funcionando.
          <div className="acoes-linha">
            <button className="btn btn-pequeno" onClick={() => this.setState({ falhou: false })}>Tentar de novo</button>
            {this.props.acao}
          </div>
        </Aviso>
      </div>
    )
  }
}
