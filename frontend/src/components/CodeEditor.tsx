import { lazy, Suspense, type ComponentProps } from 'react'

// O CodeMirror é a maior dependência do frontend e só é necessário quando há código Python na tela:
// carregado sob demanda, fica fora do pacote inicial.
const Interno = lazy(() => import('./CodeEditorInterno'))

export type CodeEditorProps = ComponentProps<typeof Interno>

export function CodeEditor(props: CodeEditorProps) {
  return (
    <Suspense fallback={<div className="editor-codigo editor-carregando" style={{ minHeight: props.altura ?? 280 }} role="status">Carregando o editor de código…</div>}>
      <Interno {...props} />
    </Suspense>
  )
}
