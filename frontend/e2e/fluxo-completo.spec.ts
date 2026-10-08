import { expect, test } from '@playwright/test'
import path from 'node:path'
import fs from 'node:fs'
import os from 'node:os'
import { adicionarBloco, criarProjeto, EXEMPLOS, ligar, moverNo, porta, rotulo } from './ajudas'

test('criar → arrastar blocos → conectar → salvar → reabrir → executar → ver o resultado', async ({ page }) => {
  await criarProjeto(page, 'Soma pelo navegador')

  // 1) um bloco chega à área de trabalho ARRASTANDO da biblioteca
  await page.getByRole('button', { name: /^Adicionar bloco Valor constante\./ })
    .dragTo(page.locator('.react-flow__pane'), { targetPosition: { x: 80, y: 90 } })
  await expect(page.locator('.react-flow__node')).toHaveCount(1)

  // 2) os demais são adicionados pelo teclado/clique e posicionados
  await adicionarBloco(page, 'Valor constante', 2)
  await moverNo(page, page.locator('.react-flow__node.selected'), 380, 360)
  await adicionarBloco(page, 'Operação matemática', 3)
  await moverNo(page, page.locator('.react-flow__node.selected'), 700, 240)
  await adicionarBloco(page, 'Saída final', 4)
  await moverNo(page, page.locator('.react-flow__node.selected'), 1000, 240)

  // 3) configura cada bloco no painel da direita
  const nos = page.locator('.react-flow__node')
  for (const [indice, nome, valor] of [[0, 'Valor A', '2'], [1, 'Valor B', '3']] as const) {
    await nos.nth(indice).locator('.no-topo').click()
    await page.getByLabel('Nome neste fluxo').fill(nome)
    await page.getByLabel('Tipo do valor').selectOption('numero')
    await page.getByLabel(rotulo('Valor')).fill(valor)
  }
  await nos.nth(3).locator('.no-topo').click()
  await page.getByLabel('Título do resultado').fill('Soma')

  // 4) conecta: duas ligações ARRASTANDO e uma pelo painel (alternativa ao mouse)
  await ligar(page, porta(page, 'Valor A', 'saída', 'Valor'), porta(page, 'Operação matemática', 'entrada', 'A'))
  await ligar(page, porta(page, 'Valor B', 'saída', 'Valor'), porta(page, 'Operação matemática', 'entrada', 'B'))
  await expect(page.locator('.react-flow__edge')).toHaveCount(2)
  await nos.nth(3).locator('.no-topo').click()
  await page.getByLabel(rotulo('Valor')).selectOption({ label: 'Operação matemática › Resultado (número)' })
  await expect(page.locator('.react-flow__edge')).toHaveCount(3)
  await expect(page.locator('.problema-chip')).toHaveCount(0)

  // 5) salva (Ctrl+S) e confere o indicador
  await expect(page.locator('.status-salvo')).toContainText('Alterações não salvas')
  await page.keyboard.press('Control+s')
  await expect(page.locator('.status-salvo')).toContainText('Tudo salvo')
  const antes = await nos.evaluateAll((els) => els.map((e) => [(e as HTMLElement).dataset.id, (e as HTMLElement).style.transform]))

  // 6) reabre (recarrega a página): blocos, posições, parâmetros e conexões permanecem
  await page.reload()
  await expect(nos).toHaveCount(4)
  await expect(page.locator('.react-flow__edge')).toHaveCount(3)
  const depois = await nos.evaluateAll((els) => els.map((e) => [(e as HTMLElement).dataset.id, (e as HTMLElement).style.transform]))
  expect(depois).toEqual(antes)
  await page.locator('.react-flow__node', { hasText: 'Valor A' }).locator('.no-topo').click()
  await expect(page.getByLabel(rotulo('Valor'))).toHaveValue('2')
  await expect(page.getByLabel('Tipo do valor')).toHaveValue('numero')
  await expect(page.locator('.status-salvo')).toContainText('Tudo salvo')

  // 7) executa e vê o resultado + o estado de cada bloco
  await page.locator('.barra').getByRole('button', { name: 'Executar', exact: true }).click()
  const resultado = page.getByRole('tabpanel', { name: 'Resultado' })
  await expect(resultado.locator('.saida-final')).toContainText('Soma')
  await expect(resultado.locator('.saida-final')).toContainText('5')
  await expect(page.locator('.react-flow__node .estado-concluido')).toHaveCount(4)
  await page.getByRole('tab', { name: /Histórico/ }).click()
  await expect(page.getByRole('tabpanel', { name: 'Histórico' }).locator('tbody tr')).toHaveCount(1)
})

test('condição: só o caminho escolhido executa e o outro aparece como ignorado', async ({ page }) => {
  await page.goto('/')
  await page.locator('input[type=file]').setInputFiles(path.join(EXEMPLOS, '03-condicao.json'))
  await expect(page.locator('.react-flow__node')).toHaveCount(7)

  const executar = () => page.locator('.barra').getByRole('button', { name: 'Executar', exact: true }).click()
  await executar()
  const resultado = page.getByRole('tabpanel', { name: 'Resultado' })
  await expect(resultado).toContainText('Valor com desconto')
  await expect(resultado).toContainText('225')
  const semDesconto = page.locator('.react-flow__node', { hasText: 'Saída final' }).filter({ hasText: 'Ignorado' })
  await expect(semDesconto).toHaveCount(1)
  await expect(semDesconto).toContainText('não foi escolhido')

  // outra execução, com outros dados informados na hora: agora é o outro caminho
  await page.getByRole('button', { name: 'Executar com dados…' }).click()
  const dialogo = page.getByRole('dialog', { name: 'Executar com dados' })
  await dialogo.getByRole('textbox').fill('{"valor": 150}')
  await dialogo.getByRole('button', { name: 'Executar' }).click()
  await expect(resultado).toContainText('Sem desconto')
  await expect(resultado).toContainText('150')
  await expect(resultado).not.toContainText('Valor com desconto')
  await expect(page.locator('.react-flow__node', { hasText: 'Aplicar desconto' })).toContainText('Ignorado')
  await page.getByRole('tab', { name: /Etapas/ }).click()
  await expect(page.getByRole('tabpanel', { name: 'Etapas' }).locator('.estado-ignorado')).toHaveCount(2)
})

test('rejeita ciclo e tipos incompatíveis na hora de conectar, com mensagem clara', async ({ page }) => {
  await criarProjeto(page, 'Validação ao conectar')
  await adicionarBloco(page, 'Operação matemática', 1)
  await moverNo(page, page.locator('.react-flow__node.selected'), 420, 150)
  await page.getByLabel('Nome neste fluxo').fill('Conta 1')
  await adicionarBloco(page, 'Operação matemática', 2)
  await moverNo(page, page.locator('.react-flow__node.selected'), 820, 150)
  await page.getByLabel('Nome neste fluxo').fill('Conta 2')
  await adicionarBloco(page, 'Valor constante', 3)
  await moverNo(page, page.locator('.react-flow__node.selected'), 420, 420)
  await page.getByLabel('Nome neste fluxo').fill('Um texto') // constante do tipo texto (padrão)

  await ligar(page, porta(page, 'Conta 1', 'saída', 'Resultado'), porta(page, 'Conta 2', 'entrada', 'A'))
  await expect(page.locator('.react-flow__edge')).toHaveCount(1)

  // ciclo (pelo painel): Conta 2 → Conta 1
  await page.locator('.react-flow__node', { hasText: 'Conta 1' }).locator('.no-topo').click()
  await page.getByLabel(rotulo('A')).selectOption({ label: 'Conta 2 › Resultado (número)' })
  await expect(page.locator('.toast-erro')).toContainText('Esta conexão não é permitida')
  await expect(page.locator('.toast-erro')).toContainText('cria um ciclo')
  await expect(page.locator('.react-flow__edge')).toHaveCount(1)

  // tipo incompatível (arrastando): texto → número
  await page.locator('.toast-erro .btn-icone').click()
  await ligar(page, porta(page, 'Um texto', 'saída', 'Valor'), porta(page, 'Conta 1', 'entrada', 'B'))
  await expect(page.locator('.toast-erro')).toContainText('Não é possível ligar')
  await expect(page.locator('.toast-erro')).toContainText('texto')
  await expect(page.locator('.react-flow__edge')).toHaveCount(1)

  // e no painel a opção incompatível aparece desabilitada
  await page.locator('.react-flow__node', { hasText: 'Conta 1' }).locator('.no-topo').click()
  await expect(page.getByLabel(rotulo('B')).locator('option', { hasText: 'Um texto › Valor (texto)' })).toBeDisabled()

  // problemas pendentes aparecem na aba e o fluxo não executa
  await page.locator('.barra').getByRole('button', { name: 'Executar', exact: true }).click()
  await expect(page.locator('.toast-erro').last()).toContainText('não foi executado')
  await expect(page.getByRole('tabpanel', { name: 'Problemas' })).toContainText('precisa estar ligada')
})

test('exportar e importar preserva o comportamento do fluxo', async ({ page }) => {
  await page.goto('/')
  await page.locator('input[type=file]').setInputFiles(path.join(EXEMPLOS, '04-lista.json'))
  await expect(page.locator('.react-flow__node')).toHaveCount(4)
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Exportar' }).click()
  const download = await downloadPromise
  // caminho ASCII: o seletor de arquivos do navegador não dispara com diretórios acentuados do outputDir
  const arquivo = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'trama-e2e-')), 'exportado.trama.json')
  await download.saveAs(arquivo)

  await page.getByRole('button', { name: 'Projetos' }).click()
  await page.locator('input[type=file]').setInputFiles(arquivo)
  await expect(page.locator('.react-flow__node')).toHaveCount(4)
  await page.locator('.barra').getByRole('button', { name: 'Executar', exact: true }).click()
  await expect(page.getByRole('tabpanel', { name: 'Resultado' })).toContainText('Preços × 3')
  await expect(page.getByRole('tabpanel', { name: 'Resultado' }).locator('pre.valor')).toContainText('90')
  await expect(page.getByRole('tabpanel', { name: 'Resultado' }).locator('pre.valor')).toContainText('30')
})

test('arquivo de importação inválido é recusado com mensagem em português', async ({ page }, info) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Seus projetos' })).toBeVisible()
  await page.waitForLoadState('networkidle') // a lista de projetos já carregou
  const antes = await page.locator('.cartao').count()
  const ruim = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'trama-e2e-')), 'ruim.json')
  fs.writeFileSync(ruim, JSON.stringify({ format: 'trama.fluxo', format_version: 1, project: { name: 'x' }, flow: { blocks: [{ id: 'a' }], connections: [] } }))
  await page.locator('input[type=file]').setInputFiles(ruim)
  await expect(page.locator('.toast-erro')).toContainText('não é um fluxo válido')
  await expect(page.locator('.cartao')).toHaveCount(antes) // nada foi criado
})
