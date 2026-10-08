import type {
  BlockDraft, BlockType, Flow, Issue, PortTypes, Project, ProjectSummary, Run, StepError, SystemInfo,
} from './types'

export class ApiFailure extends Error {
  status: number
  code: string
  suggestion?: string | null
  issues: Issue[]
  constructor(status: number, code: string, message: string, suggestion?: string | null, issues: Issue[] = []) {
    super(message)
    this.status = status
    this.code = code
    this.suggestion = suggestion
    this.issues = issues
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(`/api${path}`, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiFailure(0, 'sem_conexao', 'Não foi possível falar com o servidor da Trama.',
      'Confira se o servidor está rodando e tente de novo.')
  }
  if (resp.status === 204) return undefined as T
  let dados: any = null
  try {
    dados = await resp.json()
  } catch { /* corpo vazio ou inválido */ }
  if (!resp.ok) {
    const e = dados?.error
    throw new ApiFailure(resp.status, e?.code ?? 'erro', e?.message ?? 'Ocorreu um erro inesperado.',
      e?.suggestion, e?.issues ?? [])
  }
  return dados as T
}

export const api = {
  sistema: () => request<SystemInfo>('GET', '/sistema'),
  blocos: () => request<BlockType[]>('GET', '/blocos'),
  versaoDoBloco: (id: string, versao: number) => request<BlockType>('GET', `/blocos/${id}/versoes/${versao}`),
  exemploDoBloco: (id: string, versao: number) =>
    request<{ inputs: Record<string, unknown>; params: Record<string, unknown> }>('GET', `/blocos/${id}/exemplo?version=${versao}`),
  criarBloco: (d: BlockDraft) => request<{ block: BlockType; warnings: string[] }>('POST', '/blocos', d),
  novaVersaoDoBloco: (id: string, d: BlockDraft) => request<{ block: BlockType; warnings: string[] }>('PUT', `/blocos/${id}`, d),
  excluirBloco: (id: string) => request<void>('DELETE', `/blocos/${id}`),
  verificarCodigo: (code: string) =>
    request<{ verified: boolean; ok: boolean | null; error: StepError | null; message?: string }>('POST', '/blocos/verificar', { code }),
  testarBloco: (corpo: {
    draft?: BlockDraft; ref?: { type: string; version: number }
    params: Record<string, unknown>; inputs: Record<string, unknown>; project_id?: string | null
  }) => request<Run>('POST', '/blocos/testar', corpo),

  projetos: () => request<ProjectSummary[]>('GET', '/projetos'),
  projeto: (id: string) => request<Project>('GET', `/projetos/${id}`),
  criarProjeto: (name: string, description = '') => request<Project>('POST', '/projetos', { name, description }),
  salvarProjeto: (id: string, corpo: { name?: string; description?: string; flow?: Flow; base_revision?: number }) =>
    request<Project>('PUT', `/projetos/${id}`, corpo),
  excluirProjeto: (id: string) => request<void>('DELETE', `/projetos/${id}`),
  importarProjeto: (conteudo: unknown) =>
    request<Project & { warnings: string[]; created_blocks: string[] }>('POST', '/projetos/importar', conteudo),
  exportarFluxo: (name: string, description: string, flow: Flow) =>
    request<unknown>('POST', '/fluxos/exportar', { name, description, flow }),

  validar: (flow: Flow) =>
    request<{ valid: boolean; issues: Issue[]; port_types: PortTypes }>('POST', '/fluxos/validar', { flow }),
  validarConexao: (flow: Flow, connection: Flow['connections'][number]) =>
    request<{ ok: boolean; issues: Issue[] }>('POST', '/fluxos/validar-conexao', { flow, connection }),

  executar: (projectId: string, flow: Flow, initial_data?: Record<string, unknown>) =>
    request<Run>('POST', `/projetos/${projectId}/execucoes`, { flow, initial_data }),
  execucao: (id: string) => request<Run>('GET', `/execucoes/${id}`),
  historico: (projectId: string) => request<Run[]>('GET', `/projetos/${projectId}/execucoes`),
}
