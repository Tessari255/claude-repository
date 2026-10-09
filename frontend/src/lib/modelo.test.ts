import { describe, expect, it } from 'vitest'
import type { BlockType, Flow, Passo, PortTypes, Regra, Run } from '../types'
import {
  achar, atualizarPasso, campoDePartes, campoVazio, conteudoDinamicoPara, definicaoEfetiva, duplicar, inserir, linhaDoPasso, mover, novoPasso,
  partesDe, referenciaUnica, remover, renomearSaida, repeticoesDoLaco, tipoDaEntrada, todosOsPassos, usaOPasso, visiveisPara,
} from './modelo'

const def = (over: Partial<BlockType> & { id: string }): BlockType => ({
  version: 1, name: over.id, description: '', category: 'Dados', kind: 'builtin', icon: null, inputs: [], outputs: [], params: [],
  code: null, created_at: null, slots: [], inputs_from: null, outputs_from: null, trigger: false, ...over,
})
const saida = (id: string, over: object = {}) => ({ id, label: id, type: 'texto' as const, required: true, description: '', ...over })

const DEFS: Record<string, BlockType> = {
  gatilho: def({ id: 'gatilho', trigger: true, outputs_from: 'campos', params: [{ id: 'campos', label: 'c', type: 'portas', required: false, default: [], options: [], help: '', placeholder: '', multiline: false, allow_empty: false, min: null, max: null }] }),
  compor: def({ id: 'compor', inputs: [saida('entrada', { type: 'qualquer' })], outputs: [saida('resultado', { type: 'qualquer' })] }),
  condicao: def({ id: 'condicao', slots: [{ id: 'sim', label: 'Se sim', transparent: false, empty_hint: '' }, { id: 'nao', label: 'Se não', transparent: false, empty_hint: '' }], outputs: [saida('resultado', { type: 'booleano' })] }),
  laco: def({ id: 'laco', slots: [{ id: 'corpo', label: 'c', transparent: false, empty_hint: '' }], inputs: [saida('lista', { type: 'lista' })],
    outputs: [saida('item', { type: 'qualquer', inside: true }), saida('indice', { type: 'numero', inside: true }), saida('quantidade', { type: 'numero' })] }),
  escopo: def({ id: 'escopo', slots: [{ id: 'corpo', label: 'c', transparent: true, empty_hint: '' }], outputs: [saida('erro')] }),
}
const defDe = (p: Passo) => DEFS[p.type]

const p = (id: string, type = 'compor', extra: Partial<Passo> = {}): Passo => ({ id, type, version: 1, inputs: {}, params: {}, ...extra })

function fluxo(steps: Passo[]): Flow {
  return { schema_version: 2, trigger: p('gatilho', 'gatilho', { params: { campos: [saida('nome'), saida('n', { type: 'numero' })] } }), steps }
}

describe('campos com conteúdo dinâmico', () => {
  it('um texto sem chips volta a ser um valor fixo e trechos vizinhos se juntam', () => {
    expect(campoDePartes(['Olá, ', 'mundo'])).toEqual({ value: 'Olá, mundo' })
    expect(campoDePartes([])).toEqual({ value: '' })
    const ref = { step: 'gatilho', output: 'nome', path: '' }
    expect(campoDePartes(['a', 'b', ref, '', 'c'])).toEqual({ parts: ['ab', ref, 'c'] })
  })

  it('partesDe e referenciaUnica', () => {
    const ref = { step: 'a', output: 'x', path: '' }
    expect(partesDe({ value: 'oi' })).toEqual(['oi'])
    expect(partesDe({ value: '' })).toEqual([])
    expect(partesDe({ parts: ['a', ref] })).toEqual(['a', ref])
    expect(referenciaUnica({ parts: [ref] })).toEqual(ref)
    expect(referenciaUnica({ parts: ['a', ref] })).toBeNull()
    expect(referenciaUnica({ value: 1 })).toBeNull()
  })

  it('campo vazio espelha o servidor', () => {
    expect(campoVazio(undefined, 'texto')).toBe(true)
    expect(campoVazio({ value: '' }, 'texto')).toBe(true)
    expect(campoVazio({ value: '' }, 'qualquer')).toBe(false)
    expect(campoVazio({ value: 0 }, 'numero')).toBe(false)
    expect(campoVazio({ value: null }, 'numero')).toBe(true)
    expect(campoVazio({ parts: [''] }, 'texto')).toBe(true)
    expect(campoVazio({ parts: [{ step: 'a', output: 'b', path: '' }] }, 'texto')).toBe(false)
  })
})

describe('definição efetiva e tipos', () => {
  it('as saídas do gatilho vêm dos campos declarados', () => {
    const ef = definicaoEfetiva(DEFS.gatilho, { campos: [saida('nome'), saida('idade', { type: 'numero' })] })
    expect(ef.outputs.map((o) => o.id)).toEqual(['nome', 'idade'])
    expect(definicaoEfetiva(DEFS.gatilho, {}).outputs).toEqual([])
    expect(definicaoEfetiva(DEFS.compor, {})).toBe(DEFS.compor)
  })

  it('o tipo da entrada acompanha o parâmetro quando declarado assim', () => {
    const d = def({ id: 'v', inputs: [saida('inicial', { type: 'qualquer', type_from: { param: 'tipo' } })],
      params: [{ id: 'tipo', label: 't', type: 'selecao', required: true, default: 'texto', options: [], help: '', placeholder: '', multiline: false, allow_empty: false, min: null, max: null }] })
    expect(tipoDaEntrada(d, {}, d.inputs[0])).toBe('texto')
    expect(tipoDaEntrada(d, { tipo: 'numero' }, d.inputs[0])).toBe('numero')
    expect(tipoDaEntrada(d, { tipo: 'invalido' }, d.inputs[0])).toBe('qualquer')
  })

  it('novoPasso aplica padrões, sem compartilhar objetos com a definição', () => {
    const d = def({ id: 'x', params: [{ id: 'lista', label: 'l', type: 'lista', required: false, default: [1], options: [], help: '', placeholder: '', multiline: false, allow_empty: false, min: null, max: null }],
      inputs: [saida('q', { type: 'numero', default: 1 })], slots: DEFS.condicao.slots })
    const a = novoPasso(d), b = novoPasso(d)
    expect(a.id).not.toBe(b.id)
    expect(a.params.lista).toEqual([1]); (a.params.lista as number[]).push(2)
    expect(d.params[0].default).toEqual([1])
    expect(a.inputs.q).toEqual({ value: 1 })
    expect(a.slots).toEqual({ sim: [], nao: [] })
  })
})

describe('árvore de passos', () => {
  const base = () => fluxo([p('a'), p('c', 'condicao', { slots: { sim: [p('s1')], nao: [] } }), p('z')])

  it('percorre em ordem de documento e acha qualquer passo', () => {
    expect(todosOsPassos(base()).map((x) => x.id)).toEqual(['gatilho', 'a', 'c', 's1', 'z'])
    const pos = achar(base(), 's1')!
    expect(pos.pai?.id).toBe('c')
    expect(pos.espaco).toBe('sim')
    expect(pos.ancestrais.map((x) => x.id)).toEqual(['c'])
    expect(achar(base(), 'nada')).toBeNull()
  })

  it('insere na lista principal e dentro de ramos sem alterar o original', () => {
    const f = base()
    const f2 = inserir(f, { paiId: null, espaco: null, indice: 1 }, p('novo'))
    expect(f2.steps.map((x) => x.id)).toEqual(['a', 'novo', 'c', 'z'])
    expect(f.steps.map((x) => x.id)).toEqual(['a', 'c', 'z'])
    const f3 = inserir(f, { paiId: 'c', espaco: 'nao', indice: 0 }, p('n1'))
    expect(achar(f3, 'n1')?.espaco).toBe('nao')
    expect(f.steps[1].slots!.nao).toEqual([])
  })

  it('remove um passo e tudo o que está dentro dele', () => {
    const f = remover(base(), 'c')
    expect(todosOsPassos(f).map((x) => x.id)).toEqual(['gatilho', 'a', 'z'])
    expect(todosOsPassos(remover(base(), 's1')).map((x) => x.id)).toEqual(['gatilho', 'a', 'c', 'z'])
  })

  it('move para cima e para baixo só dentro da própria lista', () => {
    expect(mover(base(), 'z', -1).steps.map((x) => x.id)).toEqual(['a', 'z', 'c'])
    expect(mover(base(), 'a', -1).steps.map((x) => x.id)).toEqual(['a', 'c', 'z'])
    const f = inserir(base(), { paiId: 'c', espaco: 'sim', indice: 1 }, p('s2'))
    expect(achar(mover(f, 's2', -1), 's2')?.indice).toBe(0)
    expect(achar(mover(f, 's2', 1), 's2')?.indice).toBe(1)
  })

  it('duplica com ids novos, religa as referências internas e mantém as externas', () => {
    const f = fluxo([
      p('a'),
      p('c', 'condicao', { slots: { sim: [p('x'), p('y', 'compor', { inputs: { entrada: { parts: [{ step: 'x', output: 'resultado', path: '' }] } } }),
        p('w', 'compor', { inputs: { entrada: { parts: [{ step: 'a', output: 'resultado', path: '' }] } } })], nao: [] } }),
    ])
    const { flow, novoId } = duplicar(f, 'c')
    expect(flow.steps.map((x) => x.id)).toEqual(['a', 'c', novoId])
    const copia = flow.steps[2]
    const [x2, y2, w2] = copia.slots!.sim
    expect(new Set([x2.id, y2.id, w2.id, copia.id]).size).toBe(4)
    expect([x2.id, y2.id, w2.id]).not.toContain('x')
    expect(y2.inputs.entrada).toEqual({ parts: [{ step: x2.id, output: 'resultado', path: '' }] })  // interna: aponta para a cópia
    expect(w2.inputs.entrada).toEqual({ parts: [{ step: 'a', output: 'resultado', path: '' }] })    // externa: continua no original
    expect(f.steps[1].slots!.sim[1].inputs.entrada).toEqual({ parts: [{ step: 'x', output: 'resultado', path: '' }] })  // original intacto
  })

  it('renomear uma saída acompanha as referências em campos e condições', () => {
    const ref = { step: 'gatilho', output: 'nome', path: '' }
    const f = fluxo([p('a', 'compor', { inputs: { entrada: { parts: ['oi ', ref] } } }),
      p('c', 'condicao', { params: { regras: [{ esq: { parts: [ref] }, op: 'igual', dir: { parts: [ref] } }] } })])
    const g = renomearSaida(f, 'gatilho', 'nome', 'pessoa')
    expect(g.steps[0].inputs.entrada).toEqual({ parts: ['oi ', { ...ref, output: 'pessoa' }] })
    expect((g.steps[1].params.regras as Regra[])[0].esq).toEqual({ parts: [{ ...ref, output: 'pessoa' }] })
    expect((g.steps[1].params.regras as Regra[])[0].dir).toEqual({ parts: [{ ...ref, output: 'pessoa' }] })
    expect(f.steps[0].inputs.entrada).toEqual({ parts: ['oi ', ref] })
  })

  it('usaOPasso e atualizarPasso', () => {
    const f = fluxo([p('a'), p('b', 'compor', { inputs: { entrada: { parts: [{ step: 'a', output: 'resultado', path: '' }] } } })])
    expect(usaOPasso(f, 'a')).toBe(true)
    expect(usaOPasso(f, 'b')).toBe(false)
    const g = atualizarPasso(f, 'b', (x) => ({ ...x, label: 'B' }))
    expect(g.steps[1].label).toBe('B')
    expect(f.steps[1].label).toBeUndefined()
    expect(atualizarPasso(f, 'gatilho', (x) => ({ ...x, label: 'G' })).trigger.label).toBe('G')
  })
})

describe('visibilidade do conteúdo dinâmico (espelha o verificador do servidor)', () => {
  const f = fluxo([
    p('a'),
    p('c', 'condicao', { slots: { sim: [p('dentro_sim'), p('depois_dentro')], nao: [p('dentro_nao')] } }),
    p('l', 'laco', { slots: { corpo: [p('no_laco')] } }),
    p('e', 'escopo', { slots: { corpo: [p('no_escopo')] } }),
    p('z'),
  ])
  const antes = (id: string) => visiveisPara(f, id, defDe)!.antes

  it('o gatilho é visível em todo lugar e o passo vê só o que roda antes dele', () => {
    expect(antes('a')).toEqual(['gatilho'])
    expect(antes('c')).toEqual(['gatilho', 'a'])
    expect(antes('depois_dentro')).toEqual(['gatilho', 'a', 'dentro_sim'])
  })

  it('o irmão de outro ramo não é visível e o que está dentro de uma condição some depois dela', () => {
    expect(antes('dentro_nao')).toEqual(['gatilho', 'a'])
    expect(antes('l')).not.toContain('dentro_sim')
  })

  it('dentro do laço o contêiner aparece em "dentro" (item e posição); depois dele, não', () => {
    expect(visiveisPara(f, 'no_laco', defDe)!.dentro).toEqual(['l'])
    expect(antes('no_laco')).toEqual(['gatilho', 'a', 'c'])
    expect(antes('e')).toEqual(['gatilho', 'a', 'c', 'l'])
  })

  it('passos de um escopo continuam visíveis depois dele', () => {
    expect(antes('z')).toEqual(['gatilho', 'a', 'c', 'l', 'e', 'no_escopo'])
  })

  it('agrupa as saídas separando as de dentro do laço e usa os tipos efetivos da análise', () => {
    const tipos: PortTypes = { a: { inputs: {}, outputs: { resultado: 'numero' } } }
    const grupos = conteudoDinamicoPara(f, 'no_laco', defDe, tipos)
    const dentro = grupos.find((g) => g.passoId === 'l')!
    expect(dentro.dentro).toBe(true)
    expect(dentro.saidas.map((s) => s.ref.output)).toEqual(['item', 'indice'])
    expect(grupos.find((g) => g.passoId === 'a')!.saidas[0].tipo).toBe('numero')
    expect(grupos.find((g) => g.passoId === 'gatilho')!.saidas.map((s) => s.ref.output)).toEqual(['nome', 'n'])
    expect(grupos.some((g) => g.passoId === 'l' && !g.dentro)).toBe(false)  // a quantidade só existe depois do laço
    const depois = conteudoDinamicoPara(f, 'e', defDe, tipos)
    expect(depois.find((g) => g.passoId === 'l')!.saidas.map((s) => s.ref.output)).toEqual(['quantidade'])
  })
})

describe('histórico de execução com repetições', () => {
  const linha = (step_id: string, iteration: number[], state = 'concluido') => ({ step_id, iteration, position: 0, state, started_at: null, finished_at: null,
    duration_ms: 1, inputs: null, outputs: null, logs: [], error: null, skip_reason: null }) as unknown as Run['steps'][number]
  const run = { steps: [linha('a', []), linha('x', [0]), linha('x', [1], 'falhou'), linha('y', [0, 0]), linha('y', [0, 1]), linha('vazio', [])] } as unknown as Run

  it('acha a linha da repetição escolhida e cai para o prefixo mais curto', () => {
    expect(linhaDoPasso(run, 'a', [])?.state).toBe('concluido')
    expect(linhaDoPasso(run, 'x', [1])?.state).toBe('falhou')
    expect(linhaDoPasso(run, 'x', [2])).toBeNull()
    expect(linhaDoPasso(run, 'vazio', [0])?.iteration).toEqual([])   // laço com lista vazia: só existe a linha sem repetição
    expect(linhaDoPasso(null, 'a', [])).toBeNull()
  })

  it('conta as repetições de um laço (inclusive aninhado)', () => {
    const interno = p('i', 'laco', { slots: { corpo: [p('y')] } })
    const externo = p('o', 'laco', { slots: { corpo: [p('x'), interno] } })
    expect(repeticoesDoLaco(run, externo, [])).toBe(2)
    expect(repeticoesDoLaco(run, interno, [0])).toBe(2)
    expect(repeticoesDoLaco(run, interno, [1])).toBe(0)
  })
})
