import { describe, expect, it } from 'vitest'
import type { BlockType, Flow, Passo } from '../types'
import { buscarDefsFaltantes, montarDefs, ultimasVersoesDe } from './catalogo'

const def = (id: string, version: number): BlockType => ({
  id, version, name: `${id} v${version}`, description: '', category: 'Dados', kind: 'builtin', icon: null, inputs: [], outputs: [], params: [],
  code: null, created_at: null, slots: [], inputs_from: null, outputs_from: null, trigger: false,
})
const passo = (id: string, type: string, version: number, extra: Partial<Passo> = {}): Passo => ({ id, type, version, inputs: {}, params: {}, ...extra })

// gatilho v1 + compor v1 + (condição v2 com compor v1 e compor v3 dentro do ramo "sim")
const FLUXO: Flow = {
  schema_version: 2,
  trigger: passo('gatilho', 'gatilho', 1),
  steps: [
    passo('a', 'compor', 1),
    passo('c', 'condicao', 2, { slots: { sim: [passo('d', 'compor', 1), passo('e', 'compor', 3)], nao: [] } }),
  ],
}

function buscadorFalso(indisponiveis: string[] = []) {
  const chamadas: string[] = []
  const buscar = async (tipo: string, versao: number) => {
    chamadas.push(`${tipo}@${versao}`)
    if (indisponiveis.includes(`${tipo}@${versao}`)) throw new Error('excluído')
    return def(tipo, versao)
  }
  return { buscar, chamadas }
}

describe('catálogo de blocos', () => {
  it('busca só o que falta, uma vez por id@versão, inclusive dentro de ramos', async () => {
    const { buscar, chamadas } = buscadorFalso()
    const conhecidas = new Set(['gatilho@1'])
    const achadas = await buscarDefsFaltantes(FLUXO, (k) => conhecidas.has(k), buscar)
    expect(chamadas).toEqual(['compor@1', 'condicao@2', 'compor@3'])
    expect([...achadas.keys()]).toEqual(['compor@1', 'condicao@2', 'compor@3'])
    expect(achadas.get('compor@3')?.version).toBe(3)
  })

  it('não vai à rede quando o catálogo já conhece tudo', async () => {
    const { buscar, chamadas } = buscadorFalso()
    const achadas = await buscarDefsFaltantes(FLUXO, () => true, buscar)
    expect(chamadas).toEqual([])
    expect(achadas.size).toBe(0)
  })

  it('um bloco indisponível fica de fora e os outros continuam sendo buscados', async () => {
    const { buscar } = buscadorFalso(['condicao@2'])
    const achadas = await buscarDefsFaltantes(FLUXO, () => false, buscar)
    expect(achadas.has('condicao@2')).toBe(false)
    expect([...achadas.keys()].sort()).toEqual(['compor@1', 'compor@3', 'gatilho@1'])
  })

  it('montarDefs indexa a biblioteca por id@versão e acrescenta as versões antigas que o fluxo fixou', async () => {
    const biblioteca = [def('gatilho', 1), def('compor', 3), def('condicao', 2)]
    const { buscar, chamadas } = buscadorFalso()
    const mapa = await montarDefs(biblioteca, FLUXO, buscar)
    expect(chamadas).toEqual(['compor@1']) // só a versão 1 do Compor, que a biblioteca já não traz
    expect([...mapa.keys()].sort()).toEqual(['compor@1', 'compor@3', 'condicao@2', 'gatilho@1'])
    expect(mapa.get('compor@3')).toBe(biblioteca[1])
  })

  it('a última versão de cada bloco é a maior entre as listadas', () => {
    const m = ultimasVersoesDe([def('a', 1), def('b', 2), def('a', 3), def('a', 2)])
    expect(m.get('a')).toBe(3)
    expect(m.get('b')).toBe(2)
    expect(m.has('c')).toBe(false)
  })
})
