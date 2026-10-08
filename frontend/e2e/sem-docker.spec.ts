import { expect, test } from '@playwright/test'
import { adicionarBloco, criarProjeto, moverNo, semViolacoesDeAcessibilidade, rotulo } from './ajudas'

// Servidor iniciado SEM Docker (TRAMA_DOCKER_BIN inexistente): código Python fica desabilitado, sem fallback.
test('sem o executor isolado a dependência é informada e o código Python fica desabilitado', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('O executor isolado de Python não está disponível')).toBeVisible()
  await expect(page.getByText(/Instale o Docker/)).toBeVisible()
  await semViolacoesDeAcessibilidade(page)

  await criarProjeto(page, 'Sem Docker')
  await expect(page.locator('.banner-executor')).toContainText('Código Python personalizado está desabilitado')
  await expect(page.locator('.banner-executor')).toContainText('Os demais blocos continuam funcionando')

  // dá para escrever e salvar o bloco (com aviso), mas não testar
  await page.getByRole('button', { name: 'Bloco Python', exact: true }).click()
  const dialogo = page.getByRole('dialog', { name: 'Novo bloco Python' })
  await expect(dialogo).toContainText('Executor isolado indisponível')
  await expect(dialogo.getByRole('button', { name: 'Testar bloco' })).toBeDisabled()
  await dialogo.getByLabel(/Nome do bloco/).fill('Sem executor')
  await dialogo.getByRole('button', { name: 'Salvar na biblioteca' }).click()
  await expect(page.locator('.toast-sucesso')).toContainText('Sem executor')
  await expect(page.locator('.toast-sucesso')).toContainText('executor isolado está indisponível')
  await expect(page.getByText('Precisa do executor isolado').first()).toBeVisible()

  // usar o bloco: o fluxo é recusado ANTES de executar, apontando o motivo
  await adicionarBloco(page, 'Sem executor', 1)
  await moverNo(page, page.locator('.react-flow__node.selected'), 500, 200)
  await expect(page.getByRole('button', { name: 'Testar este bloco' })).toBeDisabled()
  await adicionarBloco(page, 'Saída final', 2)
  await moverNo(page, page.locator('.react-flow__node.selected'), 900, 200)
  await page.getByLabel(rotulo('Valor')).selectOption({ label: 'Sem executor › Mensagem (texto)' })
  await page.locator('.barra').getByRole('button', { name: 'Executar', exact: true }).click()
  await expect(page.locator('.toast-erro').last()).toContainText('não foi executado')
  await expect(page.getByRole('tabpanel', { name: 'Problemas' })).toContainText('executor isolado não está disponível')

  // blocos internos continuam funcionando normalmente
  await page.locator('.react-flow__node', { hasText: 'Sem executor' }).locator('.no-topo').click()
  await page.getByRole('button', { name: 'Excluir', exact: true }).click()
  await page.locator('.react-flow__node').first().locator('.no-topo').click()
  await adicionarBloco(page, 'Valor constante', 2)
  await moverNo(page, page.locator('.react-flow__node.selected'), 500, 200)
  await page.getByLabel(rotulo('Valor')).fill('olá')
  await page.locator('.react-flow__node', { hasText: 'Saída final' }).locator('.no-topo').click()
  await page.getByLabel(rotulo('Valor')).selectOption({ label: 'Valor constante › Valor (texto)' })
  await page.locator('.barra').getByRole('button', { name: 'Executar', exact: true }).click()
  await expect(page.getByRole('tabpanel', { name: 'Resultado' }).locator('.saida-texto')).toHaveText('olá')
})
