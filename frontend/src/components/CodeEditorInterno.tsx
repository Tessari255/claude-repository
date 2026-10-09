import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import { python } from '@codemirror/lang-python'
import { HighlightStyle, bracketMatching, indentOnInput, syntaxHighlighting } from '@codemirror/language'
import { EditorState, StateEffect, StateField } from '@codemirror/state'
import {
  Decoration, EditorView, drawSelection, highlightActiveLine, highlightActiveLineGutter, keymap, lineNumbers,
  type DecorationSet,
} from '@codemirror/view'
import { tags } from '@lezer/highlight'
import { useEffect, useRef } from 'react'

const definirLinhaComErro = StateEffect.define<number | null>()

const campoLinhaComErro = StateField.define<DecorationSet>({
  create: () => Decoration.none,
  update(deco, tr) {
    deco = deco.map(tr.changes)
    for (const e of tr.effects) {
      if (!e.is(definirLinhaComErro)) continue
      const n = e.value
      deco = n && n >= 1 && n <= tr.state.doc.lines
        ? Decoration.set([Decoration.line({ class: 'cm-linha-erro' }).range(tr.state.doc.line(n).from)])
        : Decoration.none
    }
    return deco
  },
  provide: (f) => EditorView.decorations.from(f),
})

// Cores com contraste mínimo de 4,5:1 sobre o fundo do editor.
const estilo = HighlightStyle.define([
  { tag: tags.keyword, color: '#A8321F', fontWeight: '600' },
  { tag: [tags.string, tags.special(tags.string)], color: '#245A3E' },
  { tag: [tags.number, tags.bool, tags.null], color: '#8A5A00' },
  { tag: tags.comment, color: '#5B6272', fontStyle: 'italic' },
  { tag: [tags.function(tags.variableName), tags.definition(tags.function(tags.variableName))], color: '#26457A' },
  { tag: tags.definition(tags.variableName), color: '#26457A' },
  { tag: [tags.className, tags.self], color: '#7B3F73' },
  { tag: tags.operator, color: '#4A5263' },
])

const tema = EditorView.theme({
  '&': { fontSize: '13.5px', backgroundColor: '#FFFDF8', color: '#1E2430', border: '1px solid #8C8269', borderRadius: '8px' },
  '&.cm-focused': { outline: '3px solid #26457A', outlineOffset: '1px' },
  '.cm-scroller': { fontFamily: "'JetBrains Mono','Cascadia Code',ui-monospace,Menlo,Consolas,monospace", lineHeight: '1.55' },
  '.cm-gutters': { backgroundColor: '#F1EADA', color: '#5B6272', border: 'none', borderRight: '1px solid #D8CFBD', borderRadius: '8px 0 0 8px' },
  '.cm-activeLine': { backgroundColor: '#F7F2E866' },
  '.cm-activeLineGutter': { backgroundColor: '#E6DDC6' },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': { backgroundColor: '#BBD0F0 !important' },
  '.cm-linha-erro': { backgroundColor: '#FBE4E0', boxShadow: 'inset 4px 0 0 #A8231B' },
})

export default function CodeEditorInterno({
  value, onChange, ariaLabel, linhaComErro, somenteLeitura = false, altura = 280,
}: {
  value: string
  onChange?: (v: string) => void
  ariaLabel: string
  linhaComErro?: number | null
  somenteLeitura?: boolean
  altura?: number
}) {
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  const aoMudar = useRef(onChange)
  aoMudar.current = onChange

  useEffect(() => {
    const v = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: value,
        extensions: [
          lineNumbers(), highlightActiveLineGutter(), highlightActiveLine(), history(), drawSelection(),
          indentOnInput(), bracketMatching(), python(), syntaxHighlighting(estilo), tema, campoLinhaComErro,
          EditorState.tabSize.of(4),
          keymap.of([indentWithTab, ...defaultKeymap, ...historyKeymap]),
          EditorState.readOnly.of(somenteLeitura),
          EditorView.contentAttributes.of({ 'aria-label': ariaLabel, 'aria-multiline': 'true' }),
          EditorView.updateListener.of((u) => { if (u.docChanged) aoMudar.current?.(u.state.doc.toString()) }),
        ],
      }),
    })
    view.current = v
    return () => { v.destroy(); view.current = null }
    // O editor é criado uma vez; mudanças de `value` são sincronizadas abaixo.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const v = view.current
    if (v && v.state.doc.toString() !== value) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } })
    }
  }, [value])

  useEffect(() => {
    const v = view.current
    if (!v) return
    v.dispatch({ effects: definirLinhaComErro.of(linhaComErro ?? null) })
    if (linhaComErro && linhaComErro >= 1 && linhaComErro <= v.state.doc.lines) {
      v.dispatch({ effects: EditorView.scrollIntoView(v.state.doc.line(linhaComErro).from, { y: 'center' }) })
    }
  }, [linhaComErro])

  return <div className="editor-codigo" ref={host} style={{ minHeight: altura, maxHeight: altura + 200 }} />
}
