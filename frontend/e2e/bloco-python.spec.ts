import { expect, test } from '@playwright/test'
import { adicionarNoFim, cartao, criarFluxo, escolherBloco, inserirConteudoDinamico, substituirCodigo, testar } from './ajudas'

test('passo de código Python no fluxo: erro com a linha, correção, resultado e salvar como bloco reutilizável', async ({ page }) => {
  await criarFluxo(page, 'Python no fluxo')
  await adicionarNoFim(page, 'Executar código Python')
  const painel = page.getByRole('complementary', { name: /^Configuração de/ })

  // Python REAL com erro: o cartão mostra "Falhou" e o painel aponta a linha e explica a causa
  await painel.getByRole('textbox', { name: 'Nome do passo' }).fill('Dividir')
  await substituirCodigo(page, 'def run(inputs, params):\n    divisor = 0\n    return {"resultado": str(10 / divisor)}\n', painel)
  const saida = await adicionarNoFim(page, 'Saída final')
  await inserirConteudoDinamico(page, saida, 'Valor', 'Resultado')
  await testar(page)
  const dividir = cartao(page, 'Dividir')
  await expect(dividir).toContainText('Falhou')
  await expect(dividir).toContainText('dividir por zero')
  await expect(cartao(page, 'Saída final')).toContainText('Ignorado')
  await dividir.locator('.cartao-principal').click()
  await page.getByRole('tab', { name: 'Execução' }).click()
  const execucao = page.getByRole('tabpanel')
  await expect(execucao).toContainText('Falhou na linha 3')
  await execucao.getByText('Detalhes técnicos', { exact: true }).click()
  await expect(execucao).toContainText('ZeroDivisionError')

  // corrige o código, usa uma entrada com conteúdo dinâmico e testa de novo
  await page.getByRole('tab', { name: 'Parâmetros' }).click()
  await substituirCodigo(page, 'def run(inputs, params):\n    print("somando", inputs["n"])\n    return {"resultado": f"o dobro é {inputs[\'n\'] * 2}"}\n', painel)
  await painel.getByText('Declarar entradas e saídas').click()
  const entrada = painel.getByRole('group', { name: 'Entrada 1' })  // o passo já nasce com uma entrada (“Nome”): renomeia e muda o tipo
  await entrada.getByLabel('Rótulo').fill('n')
  await entrada.getByLabel('Tipo').selectOption('numero')
  await expect(entrada.getByLabel('Nome no código')).toHaveValue('n')
  const gatilho = cartao(page, 'Acionar manualmente')
  await gatilho.locator('.cartao-principal').click()
  await painel.getByRole('button', { name: 'Adicionar campo' }).click()
  const campo = painel.getByRole('group', { name: 'Campo 1' })
  await campo.getByLabel('Rótulo').fill('Número')
  await campo.getByLabel('Tipo').selectOption('numero')
  await campo.getByLabel('Usar um valor padrão').check()
  await campo.getByLabel('Valor padrão de Número').fill('21')
  await dividir.locator('.cartao-principal').click()
  await inserirConteudoDinamico(page, dividir, 'n', 'Número')
  const resultado = await testar(page)
  await expect(resultado.locator('.saida-texto')).toHaveText('o dobro é 42')
  await dividir.locator('.cartao-principal').click()
  await page.getByRole('tab', { name: 'Execução' }).click()
  await expect(page.getByRole('tabpanel')).toContainText('somando 21')   // o print() virou log do passo

  // salva como bloco reutilizável e usa em outro ponto do fluxo
  await page.getByRole('tab', { name: 'Parâmetros' }).click()
  await painel.getByRole('button', { name: 'Salvar como bloco reutilizável' }).click()
  const dialogo = page.getByRole('dialog', { name: 'Novo bloco Python' })
  await dialogo.getByLabel(/Nome do bloco/).fill('Dobrar um número')
  await dialogo.getByRole('button', { name: 'Salvar na biblioteca' }).click()
  await expect(page.locator('.toast-sucesso')).toContainText('Dobrar um número')
  await page.getByRole('button', { name: 'Novo passo' }).click()
  await expect(page.getByRole('button', { name: /^Adicionar Dobrar um número\./ })).toBeVisible()
  await escolherBloco(page, 'Dobrar um número')
  await expect(cartao(page, 'Dobrar um número')).toBeVisible()
})

test('criar bloco Python na biblioteca: testar com dados de exemplo, salvar e reutilizar', async ({ page }) => {
  await criarFluxo(page, 'Usando um bloco Python')
  await page.getByRole('button', { name: 'Novo passo' }).click()
  await page.getByRole('button', { name: 'Criar bloco Python reutilizável' }).click()
  const dialogo = page.getByRole('dialog', { name: 'Novo bloco Python' })
  await expect(dialogo).toBeVisible()
  await dialogo.getByLabel(/Nome do bloco/).fill('Saudar pelo navegador')

  await substituirCodigo(page, 'def run(inputs, params):\n    divisor = 0\n    return {"mensagem": 10 / divisor}\n', dialogo)
  await dialogo.getByRole('button', { name: 'Testar bloco' }).click()
  const erro = dialogo.locator('.resultado-teste .aviso-erro')
  await expect(erro).toContainText('Erro na linha 3')
  await expect(erro).toContainText('dividir por zero')
  await expect(dialogo.locator('.cm-linha-erro')).toHaveCount(1)

  await substituirCodigo(page, 'def run(inputs, params):\n    nome = inputs.get("nome", "mundo")\n    return {"mensagem": f"Olá, {nome}!"}\n', dialogo)
  await dialogo.getByRole('button', { name: 'Testar bloco' }).click()
  await expect(dialogo.locator('.resultado-teste')).toContainText('Olá, mundo!')
  await dialogo.getByRole('button', { name: 'Salvar na biblioteca' }).click()
  await expect(page.locator('.toast-sucesso')).toContainText('Saudar pelo navegador')

  // o seletor continua aberto: o bloco novo já está lá (v1) e entra no fluxo
  await escolherBloco(page, 'Saudar pelo navegador')
  const saudar = cartao(page, 'Saudar pelo navegador')
  await expect(saudar).toContainText('v1')
  const saida = await adicionarNoFim(page, 'Saída final')
  await inserirConteudoDinamico(page, saida, 'Valor', 'Mensagem')
  const resultado = await testar(page)
  await expect(resultado.locator('.saida-texto')).toHaveText('Olá, mundo!')
})

test('editar um bloco cria nova versão e o fluxo existente continua na versão fixada', async ({ page }) => {
  await criarFluxo(page, 'Versões fixadas')
  await page.getByRole('button', { name: 'Novo passo' }).click()
  await page.getByRole('button', { name: 'Criar bloco Python reutilizável' }).click()
  let dialogo = page.getByRole('dialog', { name: 'Novo bloco Python' })
  await dialogo.getByLabel(/Nome do bloco/).fill('Versionado')
  await substituirCodigo(page, 'def run(inputs, params):\n    return {"mensagem": "versão um"}\n', dialogo)
  await dialogo.getByRole('button', { name: 'Salvar na biblioteca' }).click()
  await escolherBloco(page, 'Versionado')
  const saida = await adicionarNoFim(page, 'Saída final')
  await inserirConteudoDinamico(page, saida, 'Valor', 'Mensagem')
  await page.locator('#area-trabalho').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('Control+s')
  await expect(page.locator('.status-salvo')).toContainText('Tudo salvo')

  // edita o bloco pelo seletor → nova versão
  await page.getByRole('button', { name: 'Novo passo' }).click()
  await page.getByRole('button', { name: 'Editar o bloco Versionado' }).click()
  dialogo = page.getByRole('dialog', { name: /Editar bloco/ })
  await expect(dialogo).toContainText('será criada uma nova versão')
  await substituirCodigo(page, 'def run(inputs, params):\n    return {"mensagem": "versão dois"}\n', dialogo)
  await dialogo.getByRole('button', { name: 'Salvar nova versão' }).click()
  await expect(page.locator('.toast-sucesso').filter({ hasText: 'v2' })).toBeVisible()
  await page.keyboard.press('Escape')

  // o passo do fluxo continua na v1 e produz o mesmo resultado de antes
  const passo = cartao(page, 'Versionado')
  await expect(passo).toContainText('v1 · existe a v2')
  let resultado = await testar(page)
  await expect(resultado.locator('.saida-texto')).toHaveText('versão um')
  await passo.locator('.cartao-principal').click()
  await expect(page.getByText('Existe a v2 deste bloco')).toBeVisible()
  await expect(page.locator('.status-salvo')).toContainText('Tudo salvo') // nada mudou no fluxo

  // atualizar é uma escolha explícita
  await page.getByRole('button', { name: 'Atualizar para v2' }).click()
  resultado = await testar(page)
  await expect(resultado.locator('.saida-texto')).toHaveText('versão dois')
})

test('código com laço infinito é encerrado pelo tempo limite do passo e o erro é compreensível', async ({ page }) => {
  test.setTimeout(120_000)
  await criarFluxo(page, 'Laço infinito')
  await adicionarNoFim(page, 'Executar código Python')
  const painel = page.getByRole('complementary', { name: /^Configuração de Executar código Python/ })
  await substituirCodigo(page, 'def run(inputs, params):\n    contador = 0\n    while True:\n        contador += 1\n', painel)
  await page.getByRole('tab', { name: 'Configurações' }).click()
  await painel.getByLabel('Tempo limite do código (s)').fill('2')
  await testar(page)
  const passo = cartao(page, 'Executar código Python')
  await expect(passo).toContainText('Falhou', { timeout: 40_000 })
  await expect(passo).toContainText('tempo máximo de 2 s')
  await passo.locator('.cartao-principal').click()
  await page.getByRole('tab', { name: 'Execução' }).click()
  await expect(page.getByRole('tabpanel')).toContainText('laços sem fim')
})

test('tentar e capturar: o passo configurado para rodar após a falha trata o erro e o fluxo termina com sucesso', async ({ page }) => {
  await criarFluxo(page, 'Tratar erro')
  await adicionarNoFim(page, 'Escopo')
  await page.getByRole('button', { name: /Adicionar um passo em “Passos do escopo/ }).click()
  await escolherBloco(page, 'Executar código Python')
  const painel = page.getByRole('complementary', { name: /^Configuração de/ })
  await substituirCodigo(page, 'def run(inputs, params):\n    return {"resultado": str(1 / 0)}\n', painel)
  const captura = await adicionarNoFim(page, 'Compor')
  await page.getByRole('textbox', { name: 'Entrada' }).click()
  await page.keyboard.type('Deu erro: ')
  await inserirConteudoDinamico(page, captura, 'Entrada', 'Mensagem do erro')
  await page.getByRole('tab', { name: 'Configurações' }).click()
  await page.getByRole('checkbox', { name: 'falhou' }).check()
  await page.getByRole('checkbox', { name: 'teve sucesso' }).uncheck()
  await expect(captura).toContainText('Executar após: falhou')

  const resultado = await testar(page)
  await expect(page.locator('.cartao-passo.estado-falhou')).toHaveCount(2) // o passo Python e o escopo
  await expect(cartao(page, 'Compor')).toContainText('Concluído')
  await expect(resultado).toContainText('Concluído')                       // falha tratada: execução concluída
})
