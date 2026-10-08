import type { Estado, TipoDado } from '../types'

export const ROTULO_TIPO: Record<TipoDado, string> = {
  texto: 'texto',
  numero: 'número',
  booleano: 'sim/não',
  lista: 'lista',
  json: 'objeto JSON',
  qualquer: 'qualquer',
}

export const ROTULO_TIPO_LONGO: Record<TipoDado, string> = {
  texto: 'Texto',
  numero: 'Número',
  booleano: 'Booleano (sim/não)',
  lista: 'Lista',
  json: 'Objeto JSON',
  qualquer: 'Qualquer tipo',
}

/** Cada tipo tem cor E forma (círculo, quadrado, losango…), então não depende só de cor. */
export const FORMA_TIPO: Record<TipoDado, string> = {
  texto: 'circulo',
  numero: 'quadrado',
  booleano: 'losango',
  lista: 'triangulo',
  json: 'hexagono',
  qualquer: 'anel',
}

/** Cada categoria tem uma cor (todas passam em contraste 4,5:1 com texto branco) e o ícone do passo reforça o significado. */
export const COR_CATEGORIA: Record<string, string> = {
  Gatilhos: '#26457A',
  Controle: '#B5452E',
  'Variáveis': '#8C2F4E',
  Dados: '#1F6F8B',
  Texto: '#7B3F73',
  'Cálculo': '#8A5A00',
  Python: '#2F6F4E',
  'Saída': '#4A5263',
  Personalizados: '#2F6F4E',
}

export const ORDEM_CATEGORIAS = ['Controle', 'Variáveis', 'Dados', 'Texto', 'Cálculo', 'Python', 'Saída', 'Personalizados']

export function corDaCategoria(categoria: string): string {
  return COR_CATEGORIA[categoria] ?? '#2F6F4E'
}

export const ROTULO_ESTADO: Record<Estado, string> = {
  aguardando: 'Aguardando',
  executando: 'Executando',
  concluido: 'Concluído',
  falhou: 'Falhou',
  ignorado: 'Ignorado',
  cancelado: 'Cancelado',
}

export const ICONE_ESTADO: Record<Estado, string> = {
  aguardando: 'clock',
  executando: 'spinner',
  concluido: 'check',
  falhou: 'x',
  ignorado: 'skip',
  cancelado: 'stop',
}

/** Ícone de cada bloco interno; blocos Python (inline ou da biblioteca) usam o ícone de código. */
export const ICONE_BLOCO: Record<string, string> = {
  'builtin.gatilho_manual': 'bolt', 'builtin.condicao': 'branch', 'builtin.para_cada': 'loop', 'builtin.repetir_ate': 'loop',
  'builtin.escopo': 'scope', 'builtin.encerrar': 'stop', 'builtin.var_inicializar': 'var', 'builtin.var_definir': 'var',
  'builtin.var_incrementar': 'var', 'builtin.var_acrescentar': 'var', 'builtin.compor': 'value', 'builtin.selecionar_campos': 'pick',
  'builtin.transformar_lista': 'loop', 'builtin.texto': 'text', 'builtin.matematica': 'calc', 'builtin.python': 'python',
  'builtin.saida': 'flag',
}

export function iconeDoBloco(id: string, kind?: string): string {
  return ICONE_BLOCO[id] ?? (kind === 'python' || id.startsWith('custom.') ? 'python' : 'value')
}

export function formatarDuracao(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${ms} ms`
  return `${(ms / 1000).toLocaleString('pt-BR', { maximumFractionDigits: 2 })} s`
}

export function formatarData(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'medium' })
}

export function formatarDataRelativa(iso: string): string {
  const seg = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (seg < 45) return 'agora há pouco'
  if (seg < 3600) return `há ${Math.round(seg / 60)} min`
  if (seg < 86400) return `há ${Math.round(seg / 3600)} h`
  return new Date(iso).toLocaleDateString('pt-BR', { dateStyle: 'medium' })
}

/** Valor para exibição: textos aparecem como estão; o resto vira JSON indentado. */
export function formatarValor(v: unknown): string {
  if (typeof v === 'string') return v
  return JSON.stringify(v, null, 2) ?? 'vazio'
}

export function slugDeId(texto: string): string {
  const base = texto
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40)
  return /^[a-z_]/.test(base) ? base : base ? `_${base}` : ''
}

const ROTULO_ORIGEM: Record<string, string> = { stdout: 'saída', stderr: 'erro', system: 'sistema' }

/** Rótulo de uma origem de log. Nunca confia no valor recebido (ex.: "__proto__" não pode quebrar a tela). */
export function rotuloOrigem(origem: unknown): string {
  return typeof origem === 'string' && Object.hasOwn(ROTULO_ORIGEM, origem) ? ROTULO_ORIGEM[origem] : 'saída'
}

export function classeOrigem(origem: unknown): string {
  return typeof origem === 'string' && Object.hasOwn(ROTULO_ORIGEM, origem) ? origem : 'stdout'
}
