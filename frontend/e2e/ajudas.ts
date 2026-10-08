import { expect, type Locator, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const EXEMPLOS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../examples')

export async function criarFluxo(page: Page, nome: string) {
  await page.goto('/')
  await page.getByRole('button', { name: 'Novo fluxo' }).click()
  await page.getByLabel('Nome do fluxo').fill(nome)
  await page.getByRole('button', { name: 'Criar e abrir' }).click()
  await expect(page.locator('#area-trabalho')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Novo passo' })).toBeVisible()
}

/** Importa um arquivo de exemplo pela tela inicial e abre o fluxo criado. */
export async function importarExemplo(page: Page, arquivo: string) {
  await page.goto('/')
  await page.locator('input[type=file]').setInputFiles(path.join(EXEMPLOS, arquivo))
  await expect(page.locator('#area-trabalho')).toBeVisible()
}

/** O cartão de um passo (pelo nome que aparece nele). */
export function cartao(page: Page, nome: string | RegExp): Locator {
  const escapado = typeof nome === 'string' ? nome.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') : ''
  return page.getByRole('group', { name: typeof nome === 'string' ? new RegExp(`^(Passo|Gatilho) ${escapado}(,|$)`) : nome })
}

export const barra = (page: Page) => page.locator('.barra')

/** Escolhe um bloco no seletor “Adicionar um passo” (que já deve estar aberto). */
export async function escolherBloco(page: Page, nomeDoBloco: string) {
  await page.getByRole('button', { name: new RegExp(`^Adicionar ${nomeDoBloco}\\.`) }).click()
}

/** Adiciona um passo ao fim da lista principal e devolve o cartão dele. */
export async function adicionarNoFim(page: Page, nomeDoBloco: string, nomeNoCartao = nomeDoBloco) {
  await page.getByRole('button', { name: 'Novo passo' }).click()
  await escolherBloco(page, nomeDoBloco)
  const c = cartao(page, nomeNoCartao).first()
  await expect(c).toBeVisible()
  return c
}

/** Abre o seletor de conteúdo dinâmico de um campo (pelo nome do campo) e escolhe uma saída. */
export async function inserirConteudoDinamico(page: Page, doPasso: Locator, campo: string, saida: RegExp | string) {
  const painel = page.getByRole('complementary', { name: /^Configuração de/ })
  await expect(painel).toBeVisible()
  const grupo = painel.locator('.campo-dinamico', { has: page.locator('.rotulo-campo', { hasText: new RegExp(`^${campo}( \\*)?$`) }) }).first()
  await grupo.getByRole('button', { name: 'Conteúdo dinâmico', exact: true }).click()
  await painel.getByRole('button', { name: typeof saida === 'string' ? new RegExp(`^${saida}`) : saida }).first().click()
}

export async function testar(page: Page) {
  await barra(page).getByRole('button', { name: /^Test/ }).click()
  const painel = page.getByRole('complementary', { name: 'Testar o fluxo' })
  await expect(painel).toBeVisible()
  await painel.getByRole('button', { name: /^Testar( de novo)?$/ }).click()
  return painel
}

export async function substituirCodigo(page: Page, codigo: string, onde?: Locator) {
  await (onde ?? page).locator('.cm-content').first().click()
  await page.keyboard.press('Control+A')
  await page.keyboard.insertText(codigo)
}

export async function semViolacoesDeAcessibilidade(page: Page, escopo?: string) {
  let axe = new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
  if (escopo) axe = axe.include(escopo)
  const r = await axe.analyze()
  const resumo = r.violations.map((v) => `${v.id} (${v.impact}): ${v.help}\n   ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join('\n   ')}`)
  expect(resumo, `Violações de acessibilidade:\n${resumo.join('\n')}`).toEqual([])
}
