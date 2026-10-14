import { expect, test } from '@playwright/test'
import { adicionarNoFim, barra, cartao, criarFluxo, importarExemplo, inserirConteudoDinamico, semViolacoesDeAcessibilidade, testar } from './ajudas'

test('expressão Python num campo: erro de sintaxe no verificador, correção, teste e resultado', async ({ page }) => {
  await criarFluxo(page, 'Expressões pelo navegador')
  await cartao(page, 'Acionar manualmente').getByRole('button', { name: /Acionar manualmente/ }).click()
  const gatilho = page.getByRole('complementary', { name: /^Configuração de Acionar manualmente/ })
  for (const [i, rotulo, tipo, valor] of [[1, 'Nome', 'texto', '  ana souza '], [2, 'Números', 'lista', '[4, 8, 15, 16, 23, 42]']] as const) {
    await gatilho.getByRole('button', { name: 'Adicionar campo' }).click()
    const campo = gatilho.getByRole('group', { name: `Campo ${i}` })
    await campo.getByLabel('Rótulo').fill(rotulo)
    await campo.getByLabel('Tipo').selectOption(tipo)
    await campo.getByLabel('Usar um valor padrão').check()
    await campo.getByLabel(`Valor padrão de ${rotulo}`).fill(valor)
  }

  // Compor: o que foi digitado antes de trocar de modo volta com Ctrl+Z
  const compor = await adicionarNoFim(page, 'Compor')
  const painel = page.getByRole('complementary', { name: /^Configuração de Compor/ })
  await painel.getByRole('textbox', { name: 'Entrada' }).click()
  await page.keyboard.type('rascunho')
  const modo = painel.getByRole('button', { name: 'Expressão' })
  await modo.click()
  await expect(modo).toHaveAttribute('aria-pressed', 'true')
  await expect(painel.getByRole('status')).toContainText('foi substituído')
  await page.keyboard.press('Control+z')
  await expect(painel.getByRole('textbox', { name: 'Entrada' })).toHaveText('rascunho')
  await expect(modo).toHaveAttribute('aria-pressed', 'false')
  await modo.click()

  // a variável vem do botão de conteúdo dinâmico; um parêntese faltando aparece no campo e no verificador
  const expressao = painel.getByRole('textbox', { name: 'Entrada (expressão Python)' })
  await painel.getByRole('button', { name: 'Conteúdo dinâmico', exact: true }).click()
  await painel.getByRole('button', { name: /^Nome/ }).first().click()
  await expect(expressao).toHaveValue('nome')
  await expect(painel.getByRole('list', { name: 'Variáveis da expressão de Entrada' })).toContainText('nome = Nome')
  await expressao.press('End')
  await page.keyboard.type('.strip().title(')
  await expect(painel.locator('.campo-erro')).toContainText('erro de escrita (sintaxe)')
  await expect(expressao).toHaveAttribute('aria-invalid', 'true')
  await barra(page).getByRole('button', { name: /Verificador/ }).click()
  await expect(page.getByRole('complementary', { name: 'Verificador de fluxo' })).toContainText('a expressão de “Entrada” tem um erro de escrita')

  await compor.locator('.cartao-principal').click()
  await expressao.press('End')
  await page.keyboard.type(')')
  await expect(painel.locator('.campo-erro')).toHaveCount(0)
  await expect(compor).toContainText('ƒ nome.strip().title()')
  await modo.hover()  // ligado e com o mouse em cima, o botão continua legível
  await semViolacoesDeAcessibilidade(page, '.painel-passo')

  // a saída mostra a média calculada por outra expressão e o nome formatado pelo Compor
  await adicionarNoFim(page, 'Saída final')
  const saida = page.getByRole('complementary', { name: /^Configuração de Saída final/ })
  await saida.getByRole('button', { name: 'Expressão' }).click()
  await saida.getByRole('button', { name: 'Conteúdo dinâmico', exact: true }).click()
  await saida.getByRole('button', { name: /^Números/ }).first().click()
  await saida.getByRole('textbox', { name: 'Valor (expressão Python)' }).fill('sum(numeros) / len(numeros)')
  await page.getByRole('textbox', { name: 'Título do resultado' }).fill('Média')
  const nome = await adicionarNoFim(page, 'Saída final')
  await inserirConteudoDinamico(page, nome, 'Valor', 'Resultado')
  await page.getByRole('textbox', { name: 'Título do resultado' }).fill('Nome')
  await expect(barra(page).getByLabel(/erros?$/)).toHaveCount(0)

  const resultado = await testar(page)
  await expect(resultado.locator('.saida-final').filter({ hasText: 'Média' })).toContainText('18')
  await expect(resultado.locator('.saida-final').filter({ hasText: 'Nome' })).toContainText('Ana Souza')
  await compor.locator('.cartao-principal').click()
  await painel.getByRole('tab', { name: 'Execução' }).click()
  await expect(painel.locator('pre.valor').first()).toContainText('"entrada": "Ana Souza"')  // o histórico guarda o valor calculado
})

test('o modelo de expressões roda pelo contêiner e mostra o resultado documentado', async ({ page }) => {
  await importarExemplo(page, '07-expressoes.json')
  await expect(cartao(page, 'Nome formatado')).toContainText('ƒ nome.strip().title()')
  const resultado = await testar(page)
  await expect(resultado.locator('.saida-final').filter({ hasText: 'Saudação' })).toContainText('Olá, Ana Souza!')
  await expect(resultado.locator('.saida-final').filter({ hasText: 'Acima da média' })).toContainText('23')
})

test('variável que saiu do texto não pesa no verificador nem no teste, e a lista mostra o passo de origem', async ({ page }) => {
  await importarExemplo(page, '07-expressoes.json')
  await page.locator('[data-passo="saudacao"] > .cartao-principal').click()
  const painel = page.getByRole('complementary', { name: /^Configuração de Saída final/ })
  const variaveis = painel.getByRole('list', { name: 'Variáveis da expressão de Valor' })
  await expect(variaveis).toContainText('nome = Resultado do passo “Nome formatado”')
  await painel.getByRole('textbox', { name: 'Valor (expressão Python)' }).fill('"Olá!"')
  await expect(variaveis).toContainText('(não usada: é ignorada)')

  // o passo de origem dela pode sair: ninguém usa o conteúdo dele, e o teste roda
  await page.getByRole('button', { name: 'Mais ações para Nome formatado' }).click()
  await page.getByRole('menuitem', { name: 'Excluir' }).click()
  const aviso = page.locator('.toast').filter({ hasText: 'excluído' })
  await expect(aviso).toBeVisible()
  await expect(aviso).not.toContainText('Outros passos usavam')
  const resultado = await testar(page)
  await expect(resultado.locator('.saida-final').filter({ hasText: 'Saudação' })).toContainText('Olá!')
})

test('o aviso de troca de modo fica no passo em que ela aconteceu, e trocar o Tipo não apaga a expressão', async ({ page }) => {
  await importarExemplo(page, '07-expressoes.json')
  const painel = page.getByRole('complementary', { name: /^Configuração de/ })
  await page.locator('[data-passo="sem_numeros"] > .cartao-principal').click()
  await painel.getByRole('button', { name: 'Expressão' }).click()
  await expect(painel.getByText('foi substituído')).toBeVisible()
  await page.locator('[data-passo="media"] > .cartao-principal').click()
  await expect(painel.getByRole('textbox', { name: 'Valor (expressão Python)' })).toHaveValue('sum(numeros) / len(numeros)')
  await expect(painel.getByText('foi substituído')).toHaveCount(0)

  await adicionarNoFim(page, 'Inicializar variável')
  const expressao = painel.getByRole('textbox', { name: 'Valor inicial (expressão Python)' })
  await painel.getByRole('button', { name: 'Expressão' }).click()
  await expressao.fill('len(numeros) * 2')
  await painel.getByRole('combobox', { name: 'Tipo', exact: true }).selectOption('numero')
  await expect(expressao).toHaveValue('len(numeros) * 2')
})
