// Catálogo de blocos que o editor conhece: as definições indexadas por id@versão (o fluxo pode usar versões
// antigas, que já não estão na biblioteca) e a versão mais nova de cada bloco. Sem React e sem rede: quem
// sabe buscar uma versão no servidor entrega a função `buscar`.

import type { BlockType, Flow } from '../types'
import { chaveDoTipo, todosOsPassos } from './modelo'

export type BuscarVersao = (tipo: string, versao: number) => Promise<BlockType>

/**
 * Busca as versões de bloco que o fluxo usa e que `tem` ainda não conhece, uma vez por id@versão.
 * Um bloco que o servidor não entrega (excluído, por exemplo) fica de fora sem derrubar os demais:
 * o cartão do passo avisa que a definição está indisponível.
 */
export async function buscarDefsFaltantes(
  flow: Flow, tem: (chave: string) => boolean, buscar: BuscarVersao,
): Promise<Map<string, BlockType>> {
  const achadas = new Map<string, BlockType>()
  for (const p of todosOsPassos(flow)) {
    const k = chaveDoTipo(p.type, p.version)
    if (tem(k) || achadas.has(k)) continue
    try { achadas.set(k, await buscar(p.type, p.version)) } catch { /* bloco indisponível: o cartão avisa */ }
  }
  return achadas
}

/** A biblioteca (uma entrada por bloco, na versão atual) mais as versões fixadas que o fluxo carregado usa. */
export async function montarDefs(biblioteca: BlockType[], flow: Flow, buscar: BuscarVersao): Promise<Map<string, BlockType>> {
  const mapa = new Map<string, BlockType>(biblioteca.map((b) => [chaveDoTipo(b.id, b.version), b]))
  const extras = await buscarDefsFaltantes(flow, (k) => mapa.has(k), buscar)
  extras.forEach((v, k) => mapa.set(k, v))
  return mapa
}

export function ultimasVersoesDe(biblioteca: BlockType[]): Map<string, number> {
  const m = new Map<string, number>()
  for (const b of biblioteca) m.set(b.id, Math.max(m.get(b.id) ?? 0, b.version))
  return m
}
