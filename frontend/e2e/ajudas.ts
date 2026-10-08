import { expect, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

export const EXEMPLOS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../examples')

/** Casa o rótulo de um campo, tolerando o marcador visual de obrigatório (" *"). */
export const rotulo = (texto: string) => new RegExp(`^${texto}( \\*)?$`)

export async function criarProjeto(page: Page, nome: string) {
  await page.goto('/')
  await page.getByRole('button', { name: 'Novo projeto' }).click()
  await page.getByLabel('Nome do projeto').fill(nome)
  await page.getByRole('button', { name: 'Criar e abrir' }).click()
  await expect(page.locator('#area-trabalho')).toBeVisible()
}

/** Adiciona um bloco pela biblioteca (clique/Enter adiciona ao centro) e devolve o locator do nó. */
export async function adicionarBloco(page: Page, nomeNaBiblioteca: string, aposto: number) {
  await page.getByRole('button', { name: new RegExp(`^Adicionar bloco ${nomeNaBiblioteca}\\.`) }).click()
  await expect(page.locator('.react-flow__node')).toHaveCount(aposto)
  return page.locator('.react-flow__node.selected')
}

export async function moverNo(page: Page, no: ReturnType<Page['locator']>, x: number, y: number) {
  const caixa = (await no.locator('.no-topo').boundingBox())!
  await page.mouse.move(caixa.x + 40, caixa.y + 12)
  await page.mouse.down()
  await page.mouse.move(x, y, { steps: 12 })
  await page.mouse.up()
}

export function porta(page: Page, nomeDoBloco: string, lado: 'entrada' | 'saída', rotulo: string) {
  return page.locator('.react-flow__node', { hasText: nomeDoBloco })
    .locator(`.react-flow__handle[aria-label^="${lado === 'entrada' ? 'Entrada' : 'Saída'} ${rotulo},"]`)
}

export async function ligar(page: Page, de: ReturnType<Page['locator']>, para: ReturnType<Page['locator']>) {
  const a = (await de.boundingBox())!
  const b = (await para.boundingBox())!
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2)
  await page.mouse.down()
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 14 })
  await page.mouse.up()
}

export async function semViolacoesDeAcessibilidade(page: Page, escopo?: string) {
  let axe = new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
  if (escopo) axe = axe.include(escopo)
  const r = await axe.analyze()
  const resumo = r.violations.map((v) => `${v.id} (${v.impact}): ${v.help}\n   ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join('\n   ')}`)
  expect(resumo, `Violações de acessibilidade:\n${resumo.join('\n')}`).toEqual([])
}
