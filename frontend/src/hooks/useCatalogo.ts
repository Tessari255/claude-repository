import { useCallback, useMemo, useState } from 'react'
import { api } from '../api'
import { buscarDefsFaltantes, ultimasVersoesDe } from '../lib/catalogo'
import { chaveDoTipo } from '../lib/modelo'
import type { BlockType, Flow, Passo, SystemInfo } from '../types'
import { useVivo } from './useVivo'

export interface DadosDoCatalogo {
  defs: Map<string, BlockType>
  biblioteca: BlockType[]
  sistema: SystemInfo
}

/** Blocos que o editor conhece (biblioteca e versões fixadas pelos fluxos) e o estado do sistema (executor Docker). */
export function useCatalogo() {
  const vivo = useVivo()
  const [defs, setDefs] = useState<Map<string, BlockType>>(new Map())
  const [biblioteca, setBiblioteca] = useState<BlockType[]>([])
  const [sistema, setSistema] = useState<SystemInfo | null>(null)

  const iniciar = useCallback((d: DadosDoCatalogo) => {
    setDefs(d.defs)
    setBiblioteca(d.biblioteca)
    setSistema(d.sistema)
  }, [])

  const defDe = useCallback((p: Passo) => defs.get(chaveDoTipo(p.type, p.version)), [defs])
  const ultimasVersoes = useMemo(() => ultimasVersoesDe(biblioteca), [biblioteca])

  /** Traz as versões de bloco que o fluxo usa e o catálogo ainda não tem (uma execução antiga pode usar versões já substituídas). */
  const garantirDefs = useCallback(async (f: Flow) => {
    const achadas = await buscarDefsFaltantes(f, (k) => defs.has(k), api.versaoDoBloco)
    if (achadas.size && vivo.current) setDefs((m) => { const n = new Map(m); achadas.forEach((v, k) => n.set(k, v)); return n })
  }, [defs, vivo])

  /** Um bloco recém-escolhido no seletor entra no catálogo sem esperar outra ida ao servidor. */
  const registrar = useCallback((def: BlockType) => {
    setDefs((m) => (m.has(chaveDoTipo(def.id, def.version)) ? m : new Map(m).set(chaveDoTipo(def.id, def.version), def)))
  }, [])

  /** Depois de salvar um bloco Python: a biblioteca inteira é lida de novo e a versão salva já vale para os passos. */
  const aposSalvarBloco = useCallback(async (b: BlockType) => {
    try {
      const lista = await api.blocos()
      setBiblioteca(lista)
      setDefs((m) => {
        const n = new Map(m)
        for (const x of lista) n.set(chaveDoTipo(x.id, x.version), x)
        n.set(chaveDoTipo(b.id, b.version), b)
        return n
      })
    } catch { /* a biblioteca se atualiza na próxima carga */ }
  }, [])

  return { defs, biblioteca, sistema, iniciar, defDe, ultimasVersoes, garantirDefs, registrar, aposSalvarBloco }
}

export type Catalogo = ReturnType<typeof useCatalogo>
