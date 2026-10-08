// Tipos que espelham o contrato da API (chaves em inglês, valores em português).

export type TipoDado = 'texto' | 'numero' | 'booleano' | 'lista' | 'json' | 'qualquer'
export type TipoParametro = 'texto' | 'numero' | 'booleano' | 'lista' | 'json' | 'selecao' | 'codigo'
export type Estado = 'aguardando' | 'executando' | 'concluido' | 'falhou' | 'ignorado'

export interface PortDef {
  id: string
  label: string
  type: TipoDado
  required: boolean
  description: string
  type_from?: { param?: string | null; input?: string | null } | null
  conditional: boolean
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
}

export interface BlockInstance {
  id: string
  type: string
  version: number
  position: { x: number; y: number }
  params: Record<string, unknown>
  label: string | null
}

export interface Connection {
  id: string
  source: { block: string; port: string }
  target: { block: string; port: string }
}

export interface Flow {
  schema_version: number
  blocks: BlockInstance[]
  connections: Connection[]
  viewport: { x: number; y: number; zoom: number } | null
}

export interface Issue {
  code: string
  severity: 'erro' | 'aviso'
  scope: 'estrutura' | 'configuracao'
  message: string
  hint?: string | null
  block_id?: string | null
  port?: string | null
  param?: string | null
  connection_id?: string | null
  connection_ids?: string[]
}

export interface PortTypes {
  [blockId: string]: { inputs: Record<string, TipoDado>; outputs: Record<string, TipoDado> }
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
  block_count: number
  last_run_state: Estado | null
}

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

export interface Step {
  block_id: string
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
  block_id: string
  block_name: string
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
  result: { outputs: { block_id: string; title: string; value: unknown }[] } | null
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
