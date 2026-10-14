import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { ErrorBoundary } from './components/ErrorBoundary'
import { NotificacoesProvider } from './components/ui'
import './styles/theme.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <NotificacoesProvider>
      <ErrorBoundary titulo="A Trama encontrou um problema" acao={<button className="btn btn-pequeno" onClick={() => window.location.reload()}>Recarregar a página</button>}>
        <App />
      </ErrorBoundary>
    </NotificacoesProvider>
  </StrictMode>,
)
