// Mapa de teclas do editor, sem DOM: quem escuta o teclado só descobre se a tecla está dentro de um campo
// de texto e pergunta aqui o que ela significa. Dentro de um campo, só Ctrl+S e Ctrl+Enter valem; o resto
// (Ctrl+Z, Esc…) fica para o próprio campo, que tem o seu desfazer.

export type AcaoDeAtalho = 'salvar' | 'testar' | 'desfazer' | 'refazer' | 'fechar'

export type Tecla = { key: string; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }

export function acaoDoAtalho(t: Tecla, emCampo: boolean): AcaoDeAtalho | null {
  const ctrl = t.ctrlKey || t.metaKey
  // O Chrome dispara um keydown genérico, sem `key`, ao escolher uma sugestão de preenchimento automático.
  const k = (t.key ?? '').toLowerCase()
  if (ctrl && k === 's') return 'salvar'
  if (ctrl && t.key === 'Enter') return 'testar'
  if (emCampo) return null
  if (ctrl && k === 'z' && !t.shiftKey) return 'desfazer'
  if (ctrl && (k === 'y' || (k === 'z' && t.shiftKey))) return 'refazer'
  if (t.key === 'Escape') return 'fechar'
  return null
}

/** O navegador também reagiria a estas teclas (salvar a página, desfazer no campo…); Esc é deixada em paz. */
export const precisaImpedirPadrao = (a: AcaoDeAtalho) => a !== 'fechar'
