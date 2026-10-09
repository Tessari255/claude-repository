import { useEffect, useRef } from 'react'

/**
 * `vivo.current` fica falso depois que o componente sai de cena. Respostas de rede que chegam tarde
 * conferem isso antes de mexer no estado. O `true` no efeito rearma a marca quando o React (StrictMode,
 * em desenvolvimento) monta, desmonta e monta de novo o mesmo componente.
 */
export function useVivo() {
  const vivo = useRef(true)
  useEffect(() => {
    vivo.current = true
    return () => { vivo.current = false }
  }, [])
  return vivo
}
