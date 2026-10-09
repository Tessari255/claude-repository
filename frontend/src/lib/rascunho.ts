// Rascunho versus salvo: o que o editor decide a partir do que está na tela e do que o servidor já tem,
// sem React e sem rede. O servidor segue sendo a fonte da verdade: revisão desatualizada é recusada por ele.

import type { Flow, Issue, Project } from '../types'

/**
 * Há alterações que o servidor ainda não tem? Só faz sentido com o projeto carregado (`salvoJson` existe)
 * e fora da visão de uma execução antiga, que é somente leitura e nunca é "sujeira" do rascunho.
 */
export function estaSujo(a: {
  salvoJson: string | null; fluxoJson: string; nome: string; nomeSalvo: string | undefined; somenteLeitura: boolean
}): boolean {
  return a.salvoJson !== null && !a.somenteLeitura && (a.fluxoJson !== a.salvoJson || a.nome !== a.nomeSalvo)
}

/** Corpo do salvamento: nome em branco mantém o que já estava salvo, e a revisão de base permite ao servidor achar o conflito. */
export function corpoDoSalvamento(projeto: Pick<Project, 'name' | 'revision'>, nome: string, flow: Flow) {
  return { name: nome.trim() || projeto.name, flow, base_revision: projeto.revision }
}

export type FalhaDoSalvamento =
  | { tipo: 'conflito' }
  | { tipo: 'recusado'; detalhe: string; issues: Issue[] }

/** Conflito de revisão (outra aba salvou antes) pede decisão do usuário; qualquer outra falha vira um aviso com o primeiro problema apontado. */
export function falhaDoSalvamento(x: { code: string; message: string; issues: Issue[] }): FalhaDoSalvamento {
  if (x.code === 'conflito_de_revisao') return { tipo: 'conflito' }
  return { tipo: 'recusado', detalhe: `${x.message} ${x.issues[0]?.message ?? ''}`.trim(), issues: x.issues }
}
