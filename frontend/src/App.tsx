import { useEffect, useState } from 'react'
import { EditorPage } from './components/EditorPage'
import { Home } from './components/Home'

function rotaAtual(): { tipo: 'home' } | { tipo: 'projeto'; id: string } {
  const m = window.location.hash.match(/^#\/projeto\/([\w-]+)/)
  return m ? { tipo: 'projeto', id: m[1] } : { tipo: 'home' }
}

export function App() {
  const [rota, setRota] = useState(rotaAtual)
  useEffect(() => {
    const aoMudar = () => setRota(rotaAtual())
    window.addEventListener('hashchange', aoMudar)
    return () => window.removeEventListener('hashchange', aoMudar)
  }, [])

  // O título da página acompanha a tela, para leitores de tela e para o histórico do navegador.
  useEffect(() => { document.title = rota.tipo === 'home' ? 'Projetos — Trama' : 'Editor — Trama' }, [rota])

  if (rota.tipo === 'projeto') {
    return <EditorPage key={rota.id} projectId={rota.id} onSair={() => { window.location.hash = '#/' }} />
  }
  return <Home onAbrir={(id) => { window.location.hash = `#/projeto/${id}` }} />
}
