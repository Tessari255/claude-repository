import { useCallback, useRef, useState } from 'react'

const LIMITE = 100
const JANELA_MS = 1200

interface Estado<T> { passado: T[]; atual: T | null; futuro: T[] }

/**
 * Estado com desfazer/refazer. Alterações seguidas com a mesma `chave` (digitar em um campo) dentro de
 * uma janela curta viram uma só entrada no histórico, para Ctrl+Z desfazer a digitação inteira de uma vez.
 */
export function useHistorico<T>() {
  const [estado, setEstado] = useState<Estado<T>>({ passado: [], atual: null, futuro: [] })
  const ultima = useRef<{ chave: string; t: number } | null>(null)

  const definir = useCallback((proximo: T | ((a: T) => T), chave?: string) => {
    setEstado((e) => {
      if (e.atual === null) return e
      const novo = typeof proximo === 'function' ? (proximo as (a: T) => T)(e.atual) : proximo
      if (novo === e.atual) return e
      const agora = Date.now()
      const junta = !!chave && ultima.current?.chave === chave && agora - ultima.current.t < JANELA_MS
      ultima.current = chave ? { chave, t: agora } : null
      return {
        passado: junta ? e.passado : [...e.passado, e.atual].slice(-LIMITE),
        atual: novo,
        futuro: [],
      }
    })
  }, [])

  const reiniciar = useCallback((valor: T) => {
    ultima.current = null
    setEstado({ passado: [], atual: valor, futuro: [] })
  }, [])

  const desfazer = useCallback(() => {
    ultima.current = null
    setEstado((e) => (e.passado.length === 0 || e.atual === null ? e
      : { passado: e.passado.slice(0, -1), atual: e.passado[e.passado.length - 1], futuro: [e.atual, ...e.futuro] }))
  }, [])

  const refazer = useCallback(() => {
    ultima.current = null
    setEstado((e) => (e.futuro.length === 0 || e.atual === null ? e
      : { passado: [...e.passado, e.atual], atual: e.futuro[0], futuro: e.futuro.slice(1) }))
  }, [])

  return { atual: estado.atual, definir, reiniciar, desfazer, refazer, podeDesfazer: estado.passado.length > 0, podeRefazer: estado.futuro.length > 0 }
}
