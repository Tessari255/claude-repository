import '@xyflow/react/dist/style.css'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { NotificacoesProvider } from './components/ui'
import './styles/theme.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <NotificacoesProvider>
      <App />
    </NotificacoesProvider>
  </StrictMode>,
)
