import { expect, test } from '@playwright/test'
import { adicionarNoFim, barra, criarFluxo, inserirConteudoDinamico, semViolacoesDeAcessibilidade, testar } from './ajudas'

// Servidor iniciado SEM Docker (TRAMA_DOCKER_BIN inexistente): código Python fica desabilitado, sem fallback.
test('sem o executor isolado a dependência é informada e o código Python fica desabilitado', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('O executor isolado de Python não está disponível')).toBeVisible()
  await expect(page.getByText(/Instale o Docker/)).toBeVisible()
  await semViolacoesDeAcessibilidade(page)

  await criarFluxo(page, 'Sem Docker')
  await expect(page.locator('.banner-executor')).toContainText('Código Python está desabilitado')
  await expect(page.locator('.banner-executor')).toContainText('Os demais blocos continuam funcionando')

  // um passo de código Python entra no fluxo (dá para escrever), mas o fluxo é recusado ANTES de testar
  await adicionarNoFim(page, 'Executar código Python')
  const painel = page.getByRole('complementary', { name: /^Configuração de/ })
  await expect(painel).toContainText('O executor isolado está indisponível')
  await expect(barra(page).getByRole('button', { name: /Verificador/ }).locator('.ponto-vermelho')).toBeVisible()
  await barra(page).getByRole('button', { name: /^Test/ }).click()
  await page.getByRole('complementary', { name: 'Testar o fluxo' }).getByRole('button', { name: 'Testar', exact: true }).click()
  await expect(page.locator('.toast-erro').last()).toContainText('não foi testado')
  await expect(page.getByRole('complementary', { name: 'Verificador de fluxo' })).toContainText('executor isolado não está disponível')
  // o aviso de erro cobre a tela por alguns segundos: dispensa antes de seguir
  for (const dispensar of await page.getByRole('button', { name: 'Dispensar notificação' }).all()) await dispensar.click()
  await expect(page.locator('.toast-erro')).toHaveCount(0)

  // dá para criar um bloco Python na biblioteca (com aviso), mas não testá-lo
  await page.getByRole('button', { name: 'Novo passo' }).click()
  await page.getByRole('button', { name: 'Criar bloco Python reutilizável' }).click()
  const dialogo = page.getByRole('dialog', { name: 'Novo bloco Python' })
  await expect(dialogo).toContainText('Executor isolado indisponível')
  await expect(dialogo.getByRole('button', { name: 'Testar bloco' })).toBeDisabled()
  await dialogo.getByLabel(/Nome do bloco/).fill('Sem executor')
  await dialogo.getByRole('button', { name: 'Salvar na biblioteca' }).click()
  await expect(page.locator('.toast-sucesso')).toContainText('executor isolado está indisponível')
  await expect(page.getByText('Precisa do executor isolado').first()).toBeVisible()
  await page.keyboard.press('Escape')

  // blocos internos continuam funcionando normalmente: troca o passo Python por um Compor
  await page.getByRole('button', { name: 'Mais ações para Executar código Python' }).click()
  await page.getByRole('menuitem', { name: 'Excluir' }).click()
  const compor = await adicionarNoFim(page, 'Compor')
  await page.getByRole('textbox', { name: 'Entrada' }).fill('olá')
  const saida = await adicionarNoFim(page, 'Saída final')
  await inserirConteudoDinamico(page, saida, 'Valor', 'Resultado')
  const resultado = await testar(page)
  await expect(resultado.locator('.saida-texto')).toHaveText('olá')
  await expect(compor).toContainText('Concluído')
})
