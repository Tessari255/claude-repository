// Tipos que espelham o contrato da API (chaves em inglês, valores em português).

export type TipoDado = 'texto' | 'numero' | 'booleano' | 'lista' | 'json' | 'qualquer'
export type TipoParametro = 'texto' | 'numero' | 'booleano' | 'lista' | 'json' | 'selecao' | 'codigo' | 'regras' | 'portas' | 'variavel'
export type Estado = 'aguardando' | 'executando' | 'concluido' | 'falhou' | 'ignorado' | 'cancelado'
export type ExecutarApos = 'sucesso' | 'falhou' | 'ignorado' | 'expirou'

export interface PortDef {
  id: string
  label: string
  type: TipoDado
  required: boolean
  description: string
  type_from?: { param?: string | null; input?: string | null } | null
  conditional?: boolean
  inside?: boolean
  default?: unknown
}

export interface ParamDef {
  id: string
  label: string
  type: TipoParametro
  required: boolean
  default: unknown
  options: { value: string; label: string }[]
  help: string
  placeholder: string
  multiline: boolean
  allow_empty: boolean
  min: number | null
  max: number | null
  type_from?: { param?: string | null; input?: string | null } | null
  visible_when?: { param: string; values: string[] } | null
}

export interface SlotDef {
  id: string
  label: string
  transparent: boolean
  empty_hint: string
}

export interface BlockType {
  id: string
  version: number
  name: string
  description: string
  category: string
  kind: 'builtin' | 'python'
  icon: string | null
  inputs: PortDef[]
  outputs: PortDef[]
  params: ParamDef[]
  code: string | null
  created_at: string | null
  slots: SlotDef[]
  inputs_from: string | null
  outputs_from: string | null
  trigger: boolean
}

// ------------------------------------------------------------------ fluxo em passos
export interface Ref {
  step: string
  output: string
  path: string
}

/** O valor de um campo: valor fixo ou conteúdo dinâmico (textos soltos e referências, como os “chips” do Power Automate). */
export type Campo = { value: unknown } | { parts: (string | Ref)[] }

export interface Passo {
  id: string
  type: string
  version: number
  label?: string | null
  note?: string | null
  inputs: Record<string, Campo>
  params: Record<string, unknown>
  run_after?: ExecutarApos[]
  settings?: { retry: { count: number; interval_s: number }; timeout_s: number | null }
  slots?: Record<string, Passo[]>
}

export interface Flow {
  schema_version: 2
  trigger: Passo
  steps: Passo[]
}

export interface Regra {
  esq: Campo
  op: string
  dir?: Campo | null
}

export interface Issue {
  code: string
  severity: 'erro' | 'aviso'
  scope: 'estrutura' | 'configuracao'
  message: string
  hint?: string | null
  step_id?: string | null
  field?: string | null
}

export interface PortTypes {
  [stepId: string]: { inputs: Record<string, TipoDado>; outputs: Record<string, TipoDado> }
}

export interface Project {
  id: string
  name: string
  description: string
  revision: number
  created_at: string
  updated_at: string
  flow: Flow
}

export interface ProjectSummary extends Omit<Project, 'flow'> {
  step_count: number
  last_run_state: Estado | null
  last_run_at: string | null
  run_count: number
}

export interface Modelo {
  id: string
  name: string
  description: string
  step_count: number
  file: unknown
}

// ------------------------------------------------------------------ execuções
export interface LogChunk {
  source: 'stdout' | 'stderr' | 'system'
  text: string
}

export interface StepError {
  code: string
  message: string
  suggestion?: string | null
  technical?: {
    type?: string
    message?: string
    line?: number
    snippet?: string
    traceback?: string
    item_index?: number
  } | null
}

/** Uma linha do histórico: um passo em uma repetição (``iteration`` vazio = fora de laços). */
export interface Step {
  step_id: string
  iteration: number[]
  position: number
  state: Estado
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  inputs: Record<string, unknown> | null
  outputs: Record<string, unknown> | null
  logs: LogChunk[]
  error: StepError | null
  skip_reason: string | null
}

export interface RunError extends StepError {
  step_id: string
  step_name: string
  line?: number | null
}

export interface Run {
  id: string
  project_id: string | null
  kind: 'fluxo' | 'bloco'
  state: Estado
  created_at: string
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  trigger_inputs: Record<string, unknown>
  result: { outputs: { step_id: string; title: string; value: unknown }[]; message?: string } | null
  error: RunError | null
  steps: Step[]
}

export interface ExecutorStatus {
  disponivel: boolean
  imagem: string
  motivo: string | null
  mensagem: string | null
  instrucao: string | null
  docker_versao: string | null
}

export interface SystemInfo {
  name: string
  version: string
  executor: ExecutorStatus
  limits: { time_s: number; memory_mb: number; cpus: number; value_kb: number; logs_kb: number; max_list_items: number }
}

export interface BlockDraft {
  name: string
  description: string
  category: string
  inputs: PortDef[]
  outputs: PortDef[]
  params: ParamDef[]
  code: string
}
