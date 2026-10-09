import { useEffect, useRef } from 'react'
import { acaoDoAtalho, precisaImpedirPadrao, type AcaoDeAtalho } from '../lib/atalhos'

export type Acoes = Record<AcaoDeAtalho, () => void>

/**
 * Teclado do editor. As ações vêm de quem usa o hook e podem mudar a cada renderização: o ouvinte é
 * registrado uma vez só e chama sempre a versão mais recente. Com um diálogo aberto, o teclado é dele.
 */
export function useAtalhos(acoes: Acoes) {
  const ultimas = useRef(acoes)
  ultimas.current = acoes

  useEffect(() => {
    function aoTeclar(e: KeyboardEvent) {
      if (document.querySelector('[role="dialog"]')) return
      const alvo = e.target as HTMLElement
      const emCampo = !!alvo.closest('input, textarea, select, [contenteditable="true"], .cm-editor')
      const acao = acaoDoAtalho(e, emCampo)
      if (!acao) return
      if (precisaImpedirPadrao(acao)) e.preventDefault()
      ultimas.current[acao]()
    }
    window.addEventListener('keydown', aoTeclar)
    return () => window.removeEventListener('keydown', aoTeclar)
  }, [])
}
