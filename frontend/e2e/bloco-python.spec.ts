import { expect, test } from '@playwright/test'
import { adicionarBloco, criarProjeto, moverNo, rotulo } from './ajudas'

async function substituirCodigo(page: import('@playwright/test').Page, codigo: string) {
  await page.locator('.cm-content').click()
  await page.keyboard.press('Control+A')
  await page.keyboard.insertText(codigo)
}

test('criar bloco Python: erro com a linha, correção, testar, salvar e reutilizar em um fluxo', async ({ page }) => {
  await criarProjeto(page, 'Usando um bloco Python')
  await page.getByRole('button', { name: 'Bloco Python', exact: true }).click()
  const dialogo = page.getByRole('dialog', { name: 'Novo bloco Python' })
  await expect(dialogo).toBeVisible()
  await dialogo.getByLabel(/Nome do bloco/).fill('Saudar pelo navegador')

  // Python REAL com erro: o editor aponta a linha e a mensagem é explicada
  await substituirCodigo(page, 'def run(inputs, params):\n    divisor = 0\n    return {"mensagem": 10 / divisor}\n')
  await dialogo.getByRole('button', { name: 'Testar bloco' }).click()
  const erro = dialogo.locator('.resultado-teste .aviso-erro')
  await expect(erro).toContainText('Erro na linha 3')
  await expect(erro).toContainText('dividir por zero')
  await expect(dialogo.locator('.cm-linha-erro')).toHaveCount(1)
  await erro.getByText('Detalhes técnicos').click()
  await expect(erro).toContainText('ZeroDivisionError')

  // corrige, testa com uma entrada opcional informada e salva
  await substituirCodigo(page, 'def run(inputs, params):\n    nome = inputs.get("nome", "mundo")\n    print("saudando", nome)\n    return {"mensagem": f"Olá, {nome}!"}\n')
  await dialogo.getByRole('button', { name: 'Testar bloco' }).click()
  await expect(dialogo.locator('.resultado-teste')).toContainText('Olá, mundo!')
  await expect(dialogo.locator('.resultado-teste')).toContainText('saudando mundo')
  await expect(dialogo.locator('.cm-linha-erro')).toHaveCount(0)
  await dialogo.getByLabel(/Informar “Nome”/).check()
  await dialogo.getByLabel('Entrada “Nome”').fill('Bia')
  await dialogo.getByRole('button', { name: 'Testar bloco' }).click()
  await expect(dialogo.locator('.resultado-teste')).toContainText('Olá, Bia!')
  await dialogo.getByRole('button', { name: 'Salvar na biblioteca' }).click()
  await expect(page.locator('.toast-sucesso')).toContainText('Saudar pelo navegador')
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // o bloco está na biblioteca (versão 1) e pode ser usado em outro lugar do fluxo
  await expect(page.getByRole('button', { name: /^Adicionar bloco Saudar pelo navegador\./ })).toBeVisible()
  await adicionarBloco(page, 'Saudar pelo navegador', 1)
  await moverNo(page, page.locator('.react-flow__node.selected'), 500, 200)
  await adicionarBloco(page, 'Saída final', 2)
  await moverNo(page, page.locator('.react-flow__node.selected'), 900, 200)
  await page.getByLabel(rotulo('Valor')).selectOption({ label: 'Saudar pelo navegador › Mensagem (texto)' })
  await page.locator('.barra').getByRole('button', { name: 'Executar', exact: true }).click()
  await expect(page.getByRole('tabpanel', { name: 'Resultado' }).locator('.saida-texto')).toHaveText('Olá, mundo!')
  await page.getByRole('tab', { name: /Logs/ }).click()
  await expect(page.getByRole('tabpanel', { name: 'Logs' })).toContainText('saudando mundo')

  // testar o bloco isoladamente dentro do fluxo
  await page.locator('.react-flow__node', { hasText: 'Saudar pelo navegador' }).locator('.no-topo').click()
  await page.getByRole('button', { name: 'Testar este bloco' }).click()
  const teste = page.getByRole('dialog', { name: /Testar/ })
  await teste.getByRole('button', { name: 'Testar agora' }).click()
  await expect(teste.locator('.resultado-teste')).toContainText('Olá, mundo!')
})

test('editar um bloco cria nova versão e o fluxo existente continua na versão fixada', async ({ page }) => {
  await criarProjeto(page, 'Versões fixadas')
  await page.getByRole('button', { name: 'Bloco Python', exact: true }).click()
  let dialogo = page.getByRole('dialog', { name: 'Novo bloco Python' })
  await dialogo.getByLabel(/Nome do bloco/).fill('Versionado')
  await substituirCodigo(page, 'def run(inputs, params):\n    return {"mensagem": "versão um"}\n')
  await dialogo.getByRole('button', { name: 'Salvar na biblioteca' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await adicionarBloco(page, 'Versionado', 1)
  await moverNo(page, page.locator('.react-flow__node.selected'), 500, 200)
  await adicionarBloco(page, 'Saída final', 2)
  await moverNo(page, page.locator('.react-flow__node.selected'), 900, 200)
  await page.getByLabel(rotulo('Valor')).selectOption({ label: 'Versionado › Mensagem (texto)' })
  await page.keyboard.press('Control+s')
  await expect(page.locator('.status-salvo')).toContainText('Tudo salvo')

  // edita o bloco pela biblioteca → nova versão
  await page.getByRole('button', { name: 'Editar o bloco Versionado' }).click()
  dialogo = page.getByRole('dialog', { name: /Editar bloco/ })
  await expect(dialogo).toContainText('será criada uma nova versão')
  await substituirCodigo(page, 'def run(inputs, params):\n    return {"mensagem": "versão dois"}\n')
  await dialogo.getByRole('button', { name: 'Salvar nova versão' }).click()
  await expect(page.locator('.toast-sucesso')).toContainText('v2')

  // o bloco do fluxo continua na v1, avisa que existe a v2 e produz o mesmo resultado de antes
  const no = page.locator('.react-flow__node', { hasText: 'Versionado' })
  await expect(no.locator('.no-versao')).toContainText('v1 ↑')
  await page.locator('.barra').getByRole('button', { name: 'Executar', exact: true }).click()
  await expect(page.getByRole('tabpanel', { name: 'Resultado' }).locator('.saida-texto')).toHaveText('versão um')
  await no.locator('.no-topo').click()
  await expect(page.getByText('Existe a v2 deste bloco')).toBeVisible()
  await expect(page.locator('.status-salvo')).toContainText('Tudo salvo') // nada mudou no fluxo

  // atualizar é uma escolha explícita
  await page.getByRole('button', { name: 'Atualizar para v2' }).click()
  await expect(no.locator('.no-versao')).toContainText('v2')
  await page.locator('.barra').getByRole('button', { name: 'Executar', exact: true }).click()
  await expect(page.getByRole('tabpanel', { name: 'Resultado' }).locator('.saida-texto')).toHaveText('versão dois')
})

test('código com laço infinito é encerrado pelo limite de tempo e o erro aparece de forma compreensível', async ({ page }) => {
  test.setTimeout(120_000)
  await criarProjeto(page, 'Laço infinito')
  await page.getByRole('button', { name: 'Bloco Python', exact: true }).click()
  const dialogo = page.getByRole('dialog', { name: 'Novo bloco Python' })
  await dialogo.getByLabel(/Nome do bloco/).fill('Trava')
  await substituirCodigo(page, 'def run(inputs, params):\n    contador = 0\n    while True:\n        contador += 1\n')
  await dialogo.getByRole('button', { name: 'Testar bloco' }).click()
  const erro = dialogo.locator('.resultado-teste .aviso-erro')
  await expect(erro).toContainText('tempo máximo de 10 s', { timeout: 40_000 })
  await expect(erro).toContainText('linha')
  await expect(dialogo.locator('.cm-linha-erro')).toHaveCount(1)
})
