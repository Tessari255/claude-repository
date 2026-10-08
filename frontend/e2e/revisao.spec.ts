import { expect, test } from '@playwright/test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { criarProjeto, EXEMPLOS } from './ajudas'

test('fechar o editor de bloco com alterações pede confirmação; clicar fora não descarta', async ({ page }) => {
  await criarProjeto(page, 'Descartar sem perder')
  await page.getByRole('button', { name: 'Bloco Python', exact: true }).click()
  let dialogo = page.getByRole('dialog', { name: 'Novo bloco Python' })
  await expect(dialogo).toBeVisible()
  await page.keyboard.press('Escape') // sem alterações: fecha direto
  await expect(page.getByRole('dialog')).toHaveCount(0)

  await page.getByRole('button', { name: 'Bloco Python', exact: true }).click()
  dialogo = page.getByRole('dialog', { name: 'Novo bloco Python' })
  await dialogo.getByLabel(/Nome do bloco/).fill('Meu trabalho de uma hora')
  await page.mouse.click(5, 5) // clique no fundo escurecido
  await expect(dialogo).toBeVisible()
  await page.getByLabel(/Nome do bloco/).press('Escape')
  const confirmar = page.getByRole('dialog', { name: 'Descartar as alterações?' })
  await expect(confirmar).toBeVisible()
  await confirmar.getByRole('button', { name: 'Cancelar' }).click() // continua editando
  await expect(dialogo.getByLabel(/Nome do bloco/)).toHaveValue('Meu trabalho de uma hora')
  await dialogo.getByRole('button', { name: 'Cancelar' }).click()
  await page.getByRole('dialog', { name: 'Descartar as alterações?' }).getByRole('button', { name: 'Descartar' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})

test('editar um bloco importado sem mexer nos parâmetros não perde opções, faixa nem visibilidade', async ({ page, request }) => {
  const param = (over: object) => ({ id: 'x', label: 'X', type: 'texto', required: false, default: null, options: [], help: '', placeholder: '',
    multiline: false, allow_empty: false, min: null, max: null, type_from: null, visible_when: null, ...over })
  const bloco = {
    id: 'custom.revisao', version: 1, name: 'Com parâmetros ricos', description: 'original', category: 'Personalizados', kind: 'python', icon: null,
    inputs: [], outputs: [{ id: 'r', label: 'R', type: 'texto', required: true, description: '', type_from: null, conditional: false }],
    params: [
      param({ id: 'modo', label: 'Modo', type: 'selecao', default: 'a', options: [{ value: 'a', label: 'Opção A' }, { value: 'b', label: 'Opção B' }] }),
      param({ id: 'qtd', label: 'Quantidade', type: 'numero', default: 3, min: 1, max: 9, required: true }),
      param({ id: 'extra', label: 'Extra', type: 'texto', default: 'x', visible_when: { param: 'modo', values: ['b'] }, multiline: true, placeholder: 'dica' }),
    ],
    code: 'def run(inputs, params):\n    return {"r": params.get("modo", "a")}\n',
  }
  const envelope = {
    format: 'trama.fluxo', format_version: 1, project: { name: 'Importado rico' },
    flow: { schema_version: 1, blocks: [{ id: 'b1', type: 'custom.revisao', version: 1, position: { x: 0, y: 0 }, params: {}, label: null }], connections: [], viewport: null },
    custom_blocks: [bloco],
  }
  const arquivo = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'trama-e2e-')), 'rico.json')
  fs.writeFileSync(arquivo, JSON.stringify(envelope))
  await page.goto('/')
  await page.locator('input[type=file]').setInputFiles(arquivo)
  await expect(page.locator('.react-flow__node')).toHaveCount(1)

  await page.locator('.react-flow__node').locator('.no-topo').click()
  await page.getByRole('button', { name: 'Editar bloco' }).click()
  const dialogo = page.getByRole('dialog', { name: /Editar bloco/ })
  await dialogo.getByLabel('O que ele faz').fill('só a descrição mudou')
  await dialogo.getByRole('button', { name: 'Salvar nova versão' }).click()
  await expect(page.locator('.toast-sucesso')).toContainText('v2')

  const v2 = await (await request.get('/api/blocos/custom.revisao/versoes/2')).json()
  expect(v2.description).toBe('só a descrição mudou')
  const por = Object.fromEntries(v2.params.map((p: { id: string }) => [p.id, p]))
  expect(por.modo.type).toBe('selecao')
  expect(por.modo.options).toEqual([{ value: 'a', label: 'Opção A' }, { value: 'b', label: 'Opção B' }])
  expect(por.qtd).toMatchObject({ min: 1, max: 9, required: true, default: 3 })
  expect(por.extra).toMatchObject({ visible_when: { param: 'modo', values: ['b'] }, multiline: true, placeholder: 'dica' })
})

test('a interface sobrevive a uma resposta malformada do servidor (ErrorBoundary) e rótulos hostis não quebram', async ({ page }) => {
  await page.goto('/')
  await page.locator('input[type=file]').setInputFiles(path.join(EXEMPLOS, '02-soma.json'))
  await expect(page.locator('.react-flow__node')).toHaveCount(4)
  const forjada = (kind: 'logs' | 'erro') => ({
    id: 'exe_forjada', project_id: null, kind: 'fluxo', state: 'falhou', created_at: '2026-01-01T00:00:00Z', started_at: '2026-01-01T00:00:00Z',
    finished_at: '2026-01-01T00:00:01Z', duration_ms: 5, result: { outputs: [] },
    error: { block_id: 'soma', block_name: 'Soma', code: 'x', message: 'falhou', suggestion: null, technical: null },
    steps: [{ block_id: 'soma', position: 0, state: 'falhou', started_at: null, finished_at: null, duration_ms: 1, inputs: null, outputs: null,
      logs: kind === 'logs' ? [{ source: '__proto__', text: 'texto do log' }] : [],
      error: { code: 'x', message: 'mensagem', suggestion: null, technical: kind === 'erro' ? { line: { a: 1 }, snippet: ['x'] } : null }, skip_reason: null }],
  })
  let modo: 'logs' | 'erro' = 'logs'
  await page.route('**/api/execucoes/*', (rota) => rota.fulfill({ json: forjada(modo) }))
  await page.locator('.barra').getByRole('button', { name: 'Executar', exact: true }).click()
  await page.getByRole('tab', { name: /Logs/ }).click()
  await expect(page.getByRole('tabpanel', { name: 'Logs' })).toContainText('texto do log') // "__proto__" virou o rótulo padrão
  modo = 'erro' // agora o servidor "devolve" um erro com `line` e `snippet` que não são do tipo esperado
  await page.locator('.barra').getByRole('button', { name: 'Executar', exact: true }).click()
  await page.getByRole('tab', { name: /Erros/ }).click()
  await expect(page.getByText('Não foi possível exibir os resultados')).toBeVisible() // a falha ficou contida na área de resultados
  await expect(page.locator('.barra').getByRole('button', { name: 'Salvar' })).toBeVisible() // e o editor continua de pé
  await page.getByRole('button', { name: 'Limpar resultado' }).click()
  await expect(page.getByRole('tabpanel', { name: 'Erros' })).toContainText('Os erros aparecem aqui') // voltou ao normal
})
