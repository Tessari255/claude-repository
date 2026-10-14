import { expect, test } from '@playwright/test'
import { adicionarNoFim, barra, cartao, criarFluxo, importarExemplo, semViolacoesDeAcessibilidade, testar } from './ajudas'

test('tela inicial, editor e painéis não têm violações de acessibilidade detectáveis (WCAG 2.1 AA)', async ({ page }) => {
  await importarExemplo(page, '03-condicao.json')
  await barra(page).getByRole('button', { name: 'Meus fluxos' }).click()
  await expect(page.locator('.tabela-fluxos tbody tr')).toHaveCount(1)
  await expect(page.getByRole('heading', { name: 'Comece por um modelo' })).toBeVisible()
  await semViolacoesDeAcessibilidade(page)

  await page.getByRole('button', { name: 'Abrir o fluxo Exemplo 3 — Desconto condicional' }).click()
  await expect(page.getByRole('region', { name: /^Se sim/ })).toBeVisible()
  await semViolacoesDeAcessibilidade(page)

  // painel de um passo e o seletor de conteúdo dinâmico
  await cartao(page, 'Valor acima de 200?').locator('.cartao-principal').click()
  await expect(page.getByRole('complementary', { name: /^Configuração de/ })).toBeVisible()
  await semViolacoesDeAcessibilidade(page)
  await page.getByRole('button', { name: 'Conteúdo dinâmico', exact: true }).first().click()
  await expect(page.getByRole('group', { name: 'Conteúdo dinâmico disponível' })).toBeVisible()
  await semViolacoesDeAcessibilidade(page)
  await page.getByRole('tab', { name: 'Configurações' }).click()
  await semViolacoesDeAcessibilidade(page)

  // seletor de blocos
  await page.getByRole('button', { name: 'Novo passo' }).click()
  await expect(page.getByRole('complementary', { name: 'Adicionar um passo' })).toBeVisible()
  await semViolacoesDeAcessibilidade(page)

  // teste concluído (estados de execução na tela) e histórico
  const resultado = await testar(page)
  await expect(resultado.locator('.saida-final')).toContainText('225')
  await semViolacoesDeAcessibilidade(page)
  await barra(page).getByRole('button', { name: 'Histórico' }).click()
  await semViolacoesDeAcessibilidade(page)
  await barra(page).getByRole('button', { name: /Verificador/ }).click()
  await semViolacoesDeAcessibilidade(page)

  // menu do passo aberto
  await page.getByRole('button', { name: 'Mais ações para Valor acima de 200?' }).click()
  await expect(page.getByRole('menu')).toBeVisible()
  await semViolacoesDeAcessibilidade(page)
})

test('painel de erros do verificador e passos com problemas são acessíveis', async ({ page }) => {
  await criarFluxo(page, 'Acessibilidade dos erros')
  await adicionarNoFim(page, 'Operação matemática')
  await page.getByRole('button', { name: 'Novo passo' }).click()
  await page.getByRole('button', { name: /^Adicionar Condição\./ }).click()
  await barra(page).getByRole('button', { name: /Verificador/ }).click()
  await expect(page.getByRole('complementary', { name: 'Verificador de fluxo' })).toContainText('Erros')
  await semViolacoesDeAcessibilidade(page)
})

test('diálogos (editor de bloco, atalhos) são acessíveis e fecham com Esc devolvendo o foco', async ({ page }) => {
  await criarFluxo(page, 'Acessibilidade dos diálogos')
  await page.getByRole('button', { name: 'Novo passo' }).click()
  await page.getByRole('button', { name: 'Criar bloco Python reutilizável' }).click()
  const editor = page.getByRole('dialog', { name: 'Novo bloco Python' })
  await expect(editor).toBeVisible()
  await semViolacoesDeAcessibilidade(page)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Criar bloco Python reutilizável' })).toBeFocused() // o foco volta

  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Ver atalhos de teclado' }).click()
  await semViolacoesDeAcessibilidade(page)
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: 'Ver atalhos de teclado' })).toBeFocused()
})

test('todo o fluxo principal funciona só com o teclado', async ({ page }) => {
  await criarFluxo(page, 'Só teclado')
  // o botão "Novo passo" é alcançável pelo Tab; Enter abre o seletor, que já começa na busca
  await page.getByRole('button', { name: 'Novo passo' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByLabel('Buscar blocos')).toBeFocused()
  await page.keyboard.type('compor')
  await page.keyboard.press('Tab')   // primeiro filtro de categoria
  await page.getByRole('button', { name: /^Adicionar Compor\./ }).focus()
  await page.keyboard.press('Enter')
  await expect(cartao(page, 'Compor')).toBeVisible()
  await expect(page.locator('[role=status]').filter({ hasText: 'adicionado' }).first()).toBeAttached()

  // o campo de texto aceita digitação e o seletor de conteúdo dinâmico abre pelo teclado
  const entrada = page.getByRole('textbox', { name: 'Entrada' })
  await entrada.focus()
  await page.keyboard.type('sete')
  await page.getByRole('button', { name: 'Conteúdo dinâmico', exact: true }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('group', { name: 'Conteúdo dinâmico disponível' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('group', { name: 'Conteúdo dinâmico disponível' })).toHaveCount(0)

  // menu do passo: abre com Enter, navega com as setas, fecha com Esc devolvendo o foco
  const menu = page.getByRole('button', { name: 'Mais ações para Compor' })
  await menu.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('menuitem', { name: 'Duplicar' })).toBeFocused()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('Escape')
  await expect(menu).toBeFocused()

  // Saída final + conteúdo dinâmico + teste com Ctrl+Enter
  const saida = await adicionarNoFim(page, 'Saída final')
  await page.getByRole('group', { name: /Passo Saída final/ }).locator('.cartao-principal').focus()
  await page.getByRole('button', { name: 'Conteúdo dinâmico', exact: true }).focus()
  await page.keyboard.press('Enter')
  await page.getByRole('button', { name: /^Resultado/ }).first().focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('textbox', { name: 'Valor' }).locator('.chip-dinamico')).toHaveText('Resultado')
  await page.keyboard.press('Control+Enter')
  await expect(page.getByRole('complementary', { name: 'Testar o fluxo' }).locator('.saida-texto')).toHaveText('sete')

  // as abas do painel respondem às setas
  await saida.locator('.cartao-principal').click()
  await page.getByRole('tab', { name: 'Parâmetros' }).focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: 'Configurações' })).toHaveAttribute('aria-selected', 'true')
})

test('o estado de cada passo é comunicado por texto e ícone, não só por cor', async ({ page }) => {
  await importarExemplo(page, '03-condicao.json')
  await testar(page)
  const concluido = page.locator('.cartao-passo .estado-concluido').first()
  await expect(concluido).toContainText('Concluído')
  await expect(concluido.locator('svg')).toBeVisible()
  const ignorado = page.locator('.cartao-passo .estado-ignorado').first()
  await expect(ignorado).toContainText('Ignorado')
  await expect(page.getByRole('group', { name: /Passo Saída final, ignorado/ })).toHaveCount(1) // o nome acessível do cartão traz o estado
  // as bordas dos estados usam estilos diferentes (contínua, tracejada, pontilhada, dupla), além da cor
  const estilos = await page.locator('.cartao-passo[class*="estado-"]').evaluateAll((els) =>
    [...new Set(els.map((e) => getComputedStyle(e).borderTopStyle))])
  expect(estilos.length).toBeGreaterThanOrEqual(2)
})
