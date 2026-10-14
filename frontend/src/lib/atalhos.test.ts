import { describe, expect, it } from 'vitest'
import { acaoDoAtalho, precisaImpedirPadrao, type Tecla } from './atalhos'

const tecla = (key: string, over: Partial<Tecla> = {}): Tecla => ({ key, ctrlKey: false, metaKey: false, shiftKey: false, ...over })
const ctrl = (key: string, over: Partial<Tecla> = {}) => tecla(key, { ctrlKey: true, ...over })

describe('atalhos de teclado do editor', () => {
  it('Ctrl+S salva e Ctrl+Enter testa, inclusive dentro de um campo de texto', () => {
    for (const emCampo of [false, true]) {
      expect(acaoDoAtalho(ctrl('s'), emCampo)).toBe('salvar')
      expect(acaoDoAtalho(ctrl('S'), emCampo)).toBe('salvar')
      expect(acaoDoAtalho(ctrl('Enter'), emCampo)).toBe('testar')
    }
  })

  it('o Command do Mac vale como Ctrl', () => {
    expect(acaoDoAtalho(tecla('s', { metaKey: true }), false)).toBe('salvar')
    expect(acaoDoAtalho(tecla('z', { metaKey: true }), false)).toBe('desfazer')
  })

  it('Ctrl+Z desfaz; Ctrl+Y e Ctrl+Shift+Z refazem', () => {
    expect(acaoDoAtalho(ctrl('z'), false)).toBe('desfazer')
    expect(acaoDoAtalho(ctrl('y'), false)).toBe('refazer')
    expect(acaoDoAtalho(ctrl('z', { shiftKey: true }), false)).toBe('refazer')
    expect(acaoDoAtalho(ctrl('Z', { shiftKey: true }), false)).toBe('refazer')
  })

  it('dentro de um campo, desfazer, refazer e Esc ficam para o próprio campo', () => {
    expect(acaoDoAtalho(ctrl('z'), true)).toBeNull()
    expect(acaoDoAtalho(ctrl('y'), true)).toBeNull()
    expect(acaoDoAtalho(ctrl('z', { shiftKey: true }), true)).toBeNull()
    expect(acaoDoAtalho(tecla('Escape'), true)).toBeNull()
  })

  it('Esc fecha o painel fora de campos e as demais teclas não fazem nada', () => {
    expect(acaoDoAtalho(tecla('Escape'), false)).toBe('fechar')
    expect(acaoDoAtalho(tecla('s'), false)).toBeNull()
    expect(acaoDoAtalho(tecla('z'), false)).toBeNull()
    expect(acaoDoAtalho(tecla('Enter'), false)).toBeNull()
    expect(acaoDoAtalho(ctrl('a'), false)).toBeNull()
  })

  it('um keydown sem `key` (o Chrome dispara um ao escolher uma sugestão de preenchimento) é ignorado, não lança', () => {
    const semKey = { ctrlKey: false, metaKey: false, shiftKey: false } as unknown as Tecla
    for (const emCampo of [false, true]) expect(acaoDoAtalho(semKey, emCampo)).toBeNull()
  })

  it('só Esc deixa o comportamento padrão do navegador em paz', () => {
    expect(precisaImpedirPadrao('fechar')).toBe(false)
    for (const a of ['salvar', 'testar', 'desfazer', 'refazer'] as const) expect(precisaImpedirPadrao(a)).toBe(true)
  })
})
