import { expect, test } from '@playwright/test'
import path from 'node:path'
import { adicionarBloco, criarProjeto, EXEMPLOS, semViolacoesDeAcessibilidade, rotulo } from './ajudas'

test('tela inicial e editor não têm violações de acessibilidade detectáveis (WCAG 2.1 AA)', async ({ page }) => {
  await page.goto('/')
  await page.locator('input[type=file]').setInputFiles(path.join(EXEMPLOS, '03-condicao.json'))
  await expect(page.locator('.react-flow__node')).toHaveCount(7)
  await page.getByRole('button', { name: 'Projetos' }).click()
  await expect(page.locator('.cartao')).toHaveCount(1)
  await semViolacoesDeAcessibilidade(page)

  await page.getByRole('button', { name: 'Exemplo 3 — Desconto condicional', exact: true }).click()
  await expect(page.locator('.react-flow__node')).toHaveCount(7)
  await semViolacoesDeAcessibilidade(page)

  await page.locator('.barra').getByRole('button', { name: 'Executar', exact: true }).click()
  await expect(page.getByRole('tabpanel', { name: 'Resultado' })).toContainText('225')
  await semViolacoesDeAcessibilidade(page) // com estados de execução (concluído/ignorado) na tela
  for (const aba of ['Etapas', 'Logs', 'Erros', 'Problemas', 'Histórico']) {
    await page.getByRole('tab', { name: new RegExp(aba) }).click()
    await semViolacoesDeAcessibilidade(page)
  }
  await page.locator('.react-flow__node[aria-label^="Bloco Valor acima de 200?"]').locator('.no-topo').click()
  await semViolacoesDeAcessibilidade(page) // painel de configuração de um bloco
})

test('diálogos (editor de bloco, atalhos, executar com dados) são acessíveis e fecham com Esc', async ({ page }) => {
  await criarProjeto(page, 'Acessibilidade dos diálogos')
  await page.getByRole('button', { name: 'Bloco Python', exact: true }).click()
  const editor = page.getByRole('dialog', { name: 'Novo bloco Python' })
  await expect(editor).toBeVisible()
  await semViolacoesDeAcessibilidade(page)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Bloco Python', exact: true })).toBeFocused() // o foco volta

  await page.getByRole('button', { name: 'Ver atalhos de teclado' }).click()
  await semViolacoesDeAcessibilidade(page)
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Executar com dados…' }).click()
  await semViolacoesDeAcessibilidade(page)
  await page.keyboard.press('Escape')
})

test('todo o fluxo principal funciona só com o teclado', async ({ page }) => {
  await criarProjeto(page, 'Só teclado')
  // "/" leva à busca; Tab vai ao primeiro item; Enter adiciona o bloco
  await page.keyboard.press('/')
  await expect(page.getByLabel('Buscar blocos')).toBeFocused()
  await page.keyboard.type('constante')
  await page.keyboard.press('Tab')
  await expect(page.getByRole('button', { name: /^Adicionar bloco Valor constante/ })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('.react-flow__node')).toHaveCount(1)
  await expect(page.locator('[role=status]').filter({ hasText: 'adicionado' }).first()).toBeAttached()

  // com o bloco selecionado, o painel permite configurar sem mouse
  await page.getByLabel('Tipo do valor').selectOption('numero')
  await page.getByLabel(rotulo('Valor')).fill('7')

  // duplicar (Ctrl+D) e excluir (Delete) pelo teclado, com o foco no bloco
  await page.locator('.react-flow__node').first().focus()
  await page.keyboard.press('Control+d')
  await expect(page.locator('.react-flow__node')).toHaveCount(2)
  await page.keyboard.press('Delete')
  await expect(page.locator('.react-flow__node')).toHaveCount(1)

  // Saída final + conexão pelo seletor do painel (sem arrastar)
  await page.getByLabel('Buscar blocos').fill('')
  await adicionarBloco(page, 'Saída final', 2)
  await page.getByLabel(rotulo('Valor')).selectOption({ label: 'Valor constante › Valor (número)' })
  await expect(page.locator('.react-flow__edge')).toHaveCount(1)
  await page.keyboard.press('Control+Enter')
  await expect(page.getByRole('tabpanel', { name: 'Resultado' }).locator('pre.valor')).toHaveText('7')
  // as abas respondem às setas
  await page.getByRole('tab', { name: /Resultado/ }).focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: /Etapas/ })).toHaveAttribute('aria-selected', 'true')
})

test('o estado de cada bloco é comunicado por texto e ícone, não só por cor', async ({ page }) => {
  await page.goto('/')
  await page.locator('input[type=file]').setInputFiles(path.join(EXEMPLOS, '03-condicao.json'))
  await page.locator('.barra').getByRole('button', { name: 'Executar', exact: true }).click()
  await expect(page.getByRole('tabpanel', { name: 'Resultado' })).toContainText('225')
  const concluido = page.locator('.react-flow__node .estado-concluido').first()
  await expect(concluido).toContainText('Concluído')
  await expect(concluido.locator('svg')).toBeVisible()
  const ignorado = page.locator('.react-flow__node .estado-ignorado').first()
  await expect(ignorado).toContainText('Ignorado')
  // o nó tem nome acessível com o estado
  await expect(page.locator('.react-flow__node[aria-label*="estado: Ignorado"]')).toHaveCount(1)
  // as portas têm forma além da cor
  const formas = await page.locator('.react-flow__handle.porta').evaluateAll((els) => [...new Set(els.map((e) => [...e.classList].find((c) => c.startsWith('forma-'))))])
  expect(formas.length).toBeGreaterThanOrEqual(3)
})
