import { expect, test } from '@playwright/test'
import path from 'node:path'
import fs from 'node:fs'
import os from 'node:os'
import { adicionarNoFim, barra, cartao, criarFluxo, EXEMPLOS, importarExemplo, inserirConteudoDinamico, testar } from './ajudas'

test('criar do zero → declarar o gatilho → montar passos com conteúdo dinâmico → salvar → reabrir → testar', async ({ page }) => {
  await criarFluxo(page, 'Soma pelo navegador')

  // 1) o gatilho declara dois campos numéricos com valor padrão
  await cartao(page, 'Acionar manualmente').getByRole('button', { name: /Acionar manualmente/ }).click()
  const painel = page.getByRole('complementary', { name: /^Configuração de Acionar manualmente/ })
  for (const [i, rotulo, valor] of [[1, 'Primeiro número', '2'], [2, 'Segundo número', '3']] as const) {
    await painel.getByRole('button', { name: 'Adicionar campo' }).click()
    const campo = painel.getByRole('group', { name: `Campo ${i}` })
    await campo.getByLabel('Rótulo').fill(rotulo)
    await campo.getByLabel('Tipo').selectOption('numero')
    await campo.getByLabel('Usar um valor padrão').check()
    await campo.getByLabel(`Valor padrão de ${rotulo}`).fill(valor)
  }
  await expect(painel.getByRole('group', { name: 'Campo 1' }).getByLabel('Nome técnico')).toHaveValue('primeiro_numero')

  // 2) uma operação matemática com os dois campos como conteúdo dinâmico
  const conta = await adicionarNoFim(page, 'Operação matemática')
  await inserirConteudoDinamico(page, conta, 'A', 'Primeiro número')
  await inserirConteudoDinamico(page, conta, 'B', 'Segundo número')
  await expect(conta).toContainText('{Primeiro número}')
  await expect(conta).toContainText('{Segundo número}')

  // 3) a saída mostra o resultado da conta
  const saida = await adicionarNoFim(page, 'Saída final')
  await inserirConteudoDinamico(page, saida, 'Valor', 'Resultado')
  await page.getByRole('textbox', { name: 'Título do resultado' }).fill('Soma')
  await expect(barra(page).getByLabel(/erros?$/)).toHaveCount(0)

  // 4) salva (Ctrl+S) e confere o indicador
  await page.locator('#area-trabalho').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('Control+s')
  await expect(page.locator('.status-salvo')).toContainText('Tudo salvo')

  // 5) reabre a página: gatilho, passos, parâmetros e referências permanecem
  await page.reload()
  await expect(cartao(page, 'Operação matemática')).toContainText('{Primeiro número}')
  await expect(page.locator('.cartao-passo')).toHaveCount(3)
  await expect(page.locator('.status-salvo')).toContainText('Tudo salvo')

  // 6) testa e vê o resultado e o estado de cada passo
  const resultado = await testar(page)
  await expect(resultado.locator('.saida-final')).toContainText('Soma')
  await expect(resultado.locator('.saida-final')).toContainText('5')
  await expect(page.locator('.cartao-passo .estado-concluido')).toHaveCount(3)
  await barra(page).getByRole('button', { name: 'Histórico' }).click()
  await expect(page.getByRole('complementary', { name: 'Histórico de execuções' }).locator('tbody tr')).toHaveCount(1)
})

test('o texto de um campo mistura texto digitado e conteúdo dinâmico (chips)', async ({ page }) => {
  await criarFluxo(page, 'Saudação com chips')
  await cartao(page, 'Acionar manualmente').getByRole('button', { name: /Acionar manualmente/ }).click()
  const painel = page.getByRole('complementary', { name: /^Configuração de Acionar manualmente/ })
  await painel.getByRole('button', { name: 'Adicionar campo' }).click()
  const campo = painel.getByRole('group', { name: 'Campo 1' })
  await campo.getByLabel('Rótulo').fill('Nome')
  await campo.getByLabel('Usar um valor padrão').check()
  await campo.getByLabel('Valor padrão de Nome').fill('Ana')

  const compor = await adicionarNoFim(page, 'Compor')
  const entrada = page.getByRole('textbox', { name: 'Entrada' })
  await entrada.click()
  await page.keyboard.type('Olá, ')
  await inserirConteudoDinamico(page, compor, 'Entrada', 'Nome')
  await page.keyboard.type('! Bem-vinda.')
  await expect(entrada.locator('.chip-dinamico')).toHaveText('Nome')
  await expect(compor).toContainText('Olá, {Nome}! Bem-vinda.')

  const saida = await adicionarNoFim(page, 'Saída final')
  await inserirConteudoDinamico(page, saida, 'Valor', 'Resultado')
  const resultado = await testar(page)
  await expect(resultado.locator('.saida-texto')).toHaveText('Olá, Ana! Bem-vinda.')

  // o conteúdo usado aparece como botão (acessível por teclado) e pode ser removido
  await compor.locator('.cartao-principal').click()
  await page.getByRole('button', { name: 'Opções do conteúdo dinâmico Nome' }).click()
  await page.getByRole('button', { name: 'Remover do campo' }).click()
  await expect(entrada.locator('.chip-dinamico')).toHaveCount(0)
  await expect(compor).toContainText('Olá, ! Bem-vinda.')
})

test('condição: só o caminho escolhido executa e o outro aparece como ignorado', async ({ page }) => {
  await importarExemplo(page, '03-condicao.json')
  await expect(page.getByRole('region', { name: /^Se sim/ })).toBeVisible()
  const painel = await testar(page)
  await expect(painel.locator('.saida-final')).toContainText('Valor com desconto')
  await expect(painel.locator('.saida-final')).toContainText('225')
  const semDesconto = cartao(page, 'Saída final').filter({ hasText: 'Valor sem desconto' })
  await expect(semDesconto).toContainText('Ignorado')
  await expect(semDesconto).toContainText('não foi escolhido')
  await expect(page.locator('.ramo-nao')).toContainText('não escolhido')

  // outro teste, com outro valor informado na hora: agora é o outro caminho
  await painel.getByLabel('Valor da compra').fill('150')
  await painel.getByRole('button', { name: 'Testar de novo' }).click()
  await expect(painel.locator('.saida-final h4')).toHaveText('Valor sem desconto')
  await expect(painel.locator('.saida-final')).toContainText('150')
  await expect(cartao(page, 'Aplicar desconto')).toContainText('Ignorado')
  await expect(page.locator('.ramo-sim')).toContainText('não escolhido')
})

test('o verificador de fluxo aponta o que falta, leva ao passo e some quando é corrigido', async ({ page }) => {
  await criarFluxo(page, 'Verificação')
  const conta = await adicionarNoFim(page, 'Operação matemática')
  const verificador = barra(page).getByRole('button', { name: /Verificador/ })
  await expect(verificador.locator('.ponto-vermelho')).toHaveText('2')   // A e B são obrigatórios
  await expect(conta.locator('.etiqueta-erro')).toBeVisible()
  await verificador.click()
  const lista = page.getByRole('complementary', { name: 'Verificador de fluxo' })
  await expect(lista).toContainText('o campo “A” é obrigatório')
  await expect(lista).toContainText('o campo “B” é obrigatório')

  // testar com erro é recusado, com o motivo
  await barra(page).getByRole('button', { name: /^Testar/ }).click()
  await page.getByRole('complementary', { name: 'Testar o fluxo' }).getByRole('button', { name: 'Testar', exact: true }).click()
  await expect(page.locator('.toast-erro').last()).toContainText('não foi testado')
  await expect(page.getByRole('complementary', { name: 'Verificador de fluxo' })).toBeVisible()

  // "Ir para" abre o passo; corrige os dois campos e o contador some
  await page.getByRole('complementary', { name: 'Verificador de fluxo' }).getByRole('button', { name: /^Ir para/ }).first().click()
  const edicao = page.getByRole('complementary', { name: /^Configuração de Operação matemática/ })
  await edicao.getByRole('spinbutton', { name: 'A' }).fill('1')
  await edicao.getByRole('spinbutton', { name: 'B' }).fill('2')
  await expect(verificador.locator('.ponto-vermelho')).toHaveCount(0)
  await expect(conta.locator('.etiqueta-erro')).toHaveCount(0)
})

test('duplicar, mover e excluir pelo menu do passo, com desfazer e refazer', async ({ page }) => {
  await criarFluxo(page, 'Edição de passos')
  const a = await adicionarNoFim(page, 'Compor')
  await page.getByRole('textbox', { name: 'Entrada' }).fill('primeiro')
  await adicionarNoFim(page, 'Transformar texto')
  const cartoes = page.locator('.cartao-passo:not(.cartao-gatilho)')
  await expect(cartoes).toHaveCount(2)

  await page.getByRole('button', { name: 'Mais ações para Compor' }).click()
  await page.getByRole('menuitem', { name: 'Duplicar' }).click()
  await expect(cartoes).toHaveCount(3)
  await expect(cartoes.nth(1)).toContainText('Compor')

  await page.getByRole('button', { name: 'Mais ações para Transformar texto' }).click()
  await page.getByRole('menuitem', { name: 'Mover para cima' }).click()
  await expect(cartoes.nth(1)).toContainText('Transformar texto')

  await page.getByRole('button', { name: 'Mais ações para Transformar texto' }).click()
  await page.getByRole('menuitem', { name: 'Excluir' }).click()
  await expect(cartoes).toHaveCount(2)
  await expect(page.locator('.toast').filter({ hasText: 'excluído' })).toBeVisible()

  // Ctrl+Z desfaz uma alteração por vez; Ctrl+Y refaz
  await page.locator('#area-trabalho').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('Control+z')
  await expect(cartoes).toHaveCount(3)
  await page.keyboard.press('Control+z')
  await expect(cartoes.nth(1)).toContainText('Compor')
  await page.keyboard.press('Control+y')
  await expect(cartoes.nth(1)).toContainText('Transformar texto')
  await expect(a).toBeVisible()
  await expect(barra(page).getByRole('button', { name: /Desfazer/ })).toBeEnabled()
})

test('histórico: abrir uma execução antiga mostra o fluxo da época, somente leitura', async ({ page }) => {
  await importarExemplo(page, '02-soma.json')
  await testar(page)
  await expect(page.locator('.cartao-passo .estado-concluido')).toHaveCount(3)

  // muda o fluxo depois da execução: acrescenta um passo
  await adicionarNoFim(page, 'Compor')
  await expect(page.locator('.cartao-passo:not(.cartao-gatilho)')).toHaveCount(3)

  await barra(page).getByRole('button', { name: 'Histórico' }).click()
  const historico = page.getByRole('complementary', { name: 'Histórico de execuções' })
  await historico.getByRole('button', { name: /^Abrir a execução/ }).click()
  await expect(page.locator('.banner-visao')).toContainText('execução antiga')
  await expect(page.locator('.cartao-passo:not(.cartao-gatilho)')).toHaveCount(2)  // o fluxo como era
  await expect(page.getByRole('button', { name: 'Novo passo' })).toHaveCount(0)    // somente leitura
  await expect(page.locator('.conector-mais')).toHaveCount(0)

  // o painel do passo mostra a execução daquele passo (somente leitura)
  await cartao(page, 'Somar').getByRole('button', { name: /Somar/ }).click()
  await page.getByRole('tab', { name: 'Execução' }).click()
  await expect(page.getByRole('tabpanel')).toContainText('"resultado": 5')

  await page.locator('.banner-visao').getByRole('button', { name: 'Voltar ao fluxo atual' }).click()
  await expect(page.locator('.banner-visao')).toHaveCount(0)
  await expect(page.locator('.cartao-passo:not(.cartao-gatilho)')).toHaveCount(3)
})

test('exportar e importar preserva o comportamento do fluxo', async ({ page }) => {
  await importarExemplo(page, '04-lista.json')
  const downloadPromise = page.waitForEvent('download')
  await barra(page).getByRole('button', { name: 'Exportar' }).click()
  const download = await downloadPromise
  // caminho ASCII: o seletor de arquivos do navegador não dispara com diretórios acentuados do outputDir
  const arquivo = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'trama-e2e-')), 'exportado.trama.json')
  await download.saveAs(arquivo)

  await barra(page).getByRole('button', { name: 'Meus fluxos' }).click()
  await page.locator('input[type=file]').setInputFiles(arquivo)
  await expect(page.locator('#area-trabalho')).toBeVisible()
  const resultado = await testar(page)
  await expect(resultado.locator('.saida-final h4')).toHaveText('Lista transformada')
  await expect(resultado.locator('pre.valor')).toContainText('90')
  await expect(resultado.locator('pre.valor')).toContainText('30')
})

test('os modelos da tela inicial criam um fluxo pronto para testar', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Comece por um modelo' })).toBeVisible()
  await page.getByRole('button', { name: 'Usar o modelo Exemplo 2 — Soma de dois números' }).click()
  await expect(page.locator('#area-trabalho')).toBeVisible()
  const resultado = await testar(page)
  await expect(resultado.locator('.saida-final')).toContainText('5')
})

test('arquivo de importação inválido é recusado com mensagem em português', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Meus fluxos' })).toBeVisible()
  await page.waitForLoadState('networkidle')
  const antes = await page.locator('.tabela-fluxos tbody tr').count()
  const ruim = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'trama-e2e-')), 'ruim.json')
  fs.writeFileSync(ruim, JSON.stringify({ format: 'trama.fluxo', format_version: 2, project: { name: 'x' }, flow: { steps: [{ id: 'a' }] } }))
  await page.locator('input[type=file]').setInputFiles(ruim)
  await expect(page.locator('.toast-erro')).toContainText('não é um fluxo válido')
  await expect(page.locator('.tabela-fluxos tbody tr')).toHaveCount(antes) // nada foi criado
})

test('arquivo exportado pela versão anterior (grafo de blocos) é convertido ao importar', async ({ page }) => {
  await page.goto('/')
  const antigo = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'trama-e2e-')), 'antigo.json')
  fs.writeFileSync(antigo, JSON.stringify({
    format: 'trama.fluxo', format_version: 1, project: { name: 'Fluxo antigo', description: '' }, custom_blocks: [],
    flow: {
      schema_version: 1, viewport: null,
      blocks: [
        { id: 'a', type: 'builtin.constante', version: 1, position: { x: 0, y: 0 }, params: { tipo: 'numero', valor: 20 }, label: null },
        { id: 'b', type: 'builtin.constante', version: 1, position: { x: 0, y: 100 }, params: { tipo: 'numero', valor: 22 }, label: null },
        { id: 'm', type: 'builtin.matematica', version: 1, position: { x: 300, y: 0 }, params: { operacao: 'somar' }, label: 'Somar tudo' },
        { id: 's', type: 'builtin.saida', version: 1, position: { x: 600, y: 0 }, params: { titulo: 'Total' }, label: null },
      ],
      connections: [
        { id: 'c1', source: { block: 'a', port: 'valor' }, target: { block: 'm', port: 'a' } },
        { id: 'c2', source: { block: 'b', port: 'valor' }, target: { block: 'm', port: 'b' } },
        { id: 'c3', source: { block: 'm', port: 'resultado' }, target: { block: 's', port: 'valor' } },
      ],
    },
  }))
  await page.locator('input[type=file]').setInputFiles(antigo)
  await expect(page.locator('#area-trabalho')).toBeVisible()
  await expect(cartao(page, 'Somar tudo')).toBeVisible()
  const resultado = await testar(page)
  await expect(resultado.locator('.saida-final')).toContainText('42')
})
