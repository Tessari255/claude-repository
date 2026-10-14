import { expect, test } from '@playwright/test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { adicionarNoFim, barra, cartao, criarFluxo, importarExemplo, testar } from './ajudas'

test('fechar o editor de bloco com alterações pede confirmação; clicar fora não descarta', async ({ page }) => {
  await criarFluxo(page, 'Descartar sem perder')
  await page.getByRole('button', { name: 'Novo passo' }).click()
  await page.getByRole('button', { name: 'Criar bloco Python reutilizável' }).click()
  let dialogo = page.getByRole('dialog', { name: 'Novo bloco Python' })
  await expect(dialogo).toBeVisible()
  await page.keyboard.press('Escape') // sem alterações: fecha direto
  await expect(page.getByRole('dialog')).toHaveCount(0)

  await page.getByRole('button', { name: 'Criar bloco Python reutilizável' }).click()
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
    format: 'trama.fluxo', format_version: 2, project: { name: 'Importado rico' },
    flow: { schema_version: 2, trigger: { id: 'gatilho', type: 'builtin.gatilho_manual', version: 1, params: { campos: [] } },
      steps: [{ id: 'b1', type: 'custom.revisao', version: 1, params: {} }] },
    custom_blocks: [bloco],
  }
  const arquivo = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'trama-e2e-')), 'rico.json')
  fs.writeFileSync(arquivo, JSON.stringify(envelope))
  await page.goto('/')
  await page.locator('input[type=file]').setInputFiles(arquivo)
  await expect(cartao(page, 'Com parâmetros ricos')).toBeVisible()

  await cartao(page, 'Com parâmetros ricos').locator('.cartao-principal').click()
  await page.getByRole('button', { name: 'Editar o código do bloco' }).click()
  const dialogo = page.getByRole('dialog', { name: /Editar bloco/ })
  await dialogo.getByLabel('O que ele faz').fill('só a descrição mudou')
  await dialogo.getByRole('button', { name: 'Salvar nova versão' }).click()
  await expect(page.locator('.toast-sucesso').filter({ hasText: 'v2' })).toBeVisible()

  const v2 = await (await request.get('/api/blocos/custom.revisao/versoes/2')).json()
  expect(v2.description).toBe('só a descrição mudou')
  const por = Object.fromEntries(v2.params.map((p: { id: string }) => [p.id, p]))
  expect(por.modo.type).toBe('selecao')
  expect(por.modo.options).toEqual([{ value: 'a', label: 'Opção A' }, { value: 'b', label: 'Opção B' }])
  expect(por.qtd).toMatchObject({ min: 1, max: 9, required: true, default: 3 })
  expect(por.extra).toMatchObject({ visible_when: { param: 'modo', values: ['b'] }, multiline: true, placeholder: 'dica' })
})

test('a interface sobrevive a uma resposta malformada do servidor e rótulos hostis não executam nada', async ({ page }) => {
  await importarExemplo(page, '02-soma.json')
  const forjada = (kind: 'logs' | 'erro') => ({
    id: 'exe_forjada', project_id: null, kind: 'fluxo', state: 'falhou', created_at: '2026-01-01T00:00:00Z', started_at: '2026-01-01T00:00:00Z',
    finished_at: '2026-01-01T00:00:01Z', duration_ms: 5, trigger_inputs: {}, result: { outputs: [] },
    error: { step_id: 'soma', step_name: 'Soma', code: 'x', message: 'falhou', suggestion: null, technical: null },
    steps: [{ step_id: 'soma', iteration: [], position: 0, state: 'falhou', started_at: null, finished_at: null, duration_ms: 1, inputs: null, outputs: null,
      logs: kind === 'logs' ? [{ source: '__proto__', text: 'texto do log' }] : [],
      error: { code: 'x', message: 'mensagem', suggestion: null, technical: kind === 'erro' ? { line: { a: 1 }, snippet: ['x'] } : null }, skip_reason: null }],
  })
  let modo: 'logs' | 'erro' = 'logs'
  await page.route('**/api/execucoes/*', (rota) => rota.fulfill({ json: forjada(modo) }))
  await testar(page)
  await cartao(page, 'Somar').locator('.cartao-principal').click()
  await page.getByRole('tab', { name: 'Execução' }).click()
  await expect(page.getByRole('tabpanel')).toContainText('texto do log') // "__proto__" virou o rótulo padrão

  modo = 'erro' // agora o servidor "devolve" um erro com `line` e `snippet` que não são do tipo esperado
  await page.getByRole('complementary', { name: 'Configuração de Somar' }).getByRole('button', { name: 'Fechar o painel do passo' }).click()
  await testar(page)
  await cartao(page, 'Somar').locator('.cartao-principal').click()
  await page.getByRole('tab', { name: 'Execução' }).click()
  await expect(page.getByText('Não foi possível exibir este painel')).toBeVisible() // a falha ficou contida no painel
  await expect(barra(page).getByRole('button', { name: 'Salvar' })).toBeVisible()  // e o editor continua de pé
  await expect(cartao(page, 'Somar')).toBeVisible()
})

test('nomes de passo com HTML são exibidos como texto e nada é executado', async ({ page }) => {
  await criarFluxo(page, 'Nomes hostis')
  await adicionarNoFim(page, 'Compor')
  const hostil = '<img src=x onerror="window.__xss=1"><script>window.__xss=2</script>'
  await page.getByRole('textbox', { name: 'Nome do passo' }).fill(hostil)
  await expect(page.locator('.cartao-nome', { hasText: '<img src=x' })).toBeVisible()
  await page.getByRole('textbox', { name: 'Entrada' }).fill(hostil)
  expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined()
  await expect(page.locator('.cartao-passo img[src="x"]')).toHaveCount(0)
})

test('salvar em duas abas: a segunda recebe o aviso de conflito e nada é sobrescrito', async ({ browser }) => {
  const contexto = await browser.newContext({ baseURL: 'http://127.0.0.1:8123' })
  const a = await contexto.newPage()
  await criarFluxo(a, 'Conflito entre abas')
  await a.keyboard.press('Control+s')
  const url = a.url()
  const b = await contexto.newPage()
  await b.goto(url)
  await expect(b.locator('#area-trabalho')).toBeVisible()

  await adicionarNoFim(a, 'Compor')
  await a.locator('#area-trabalho').click({ position: { x: 5, y: 5 } })
  await a.keyboard.press('Control+s')
  await expect(a.locator('.status-salvo')).toContainText('Tudo salvo')

  await adicionarNoFim(b, 'Transformar texto')
  await b.locator('#area-trabalho').click({ position: { x: 5, y: 5 } })
  await b.keyboard.press('Control+s')
  await expect(b.getByRole('dialog', { name: 'Este fluxo mudou em outra aba' })).toBeVisible()
  await b.getByRole('button', { name: 'Cancelar' }).click()

  await a.reload()
  await expect(cartao(a, 'Compor')).toBeVisible()          // a versão salva pela aba A permanece
  await expect(cartao(a, 'Transformar texto')).toHaveCount(0)
  await contexto.close()
})

test('uma execução marcada como cancelada no servidor aparece como cancelada na interface', async ({ page }) => {
  await importarExemplo(page, '05-tratar-erros.json')
  await barra(page).getByRole('button', { name: /^Test/ }).click()
  const painel = page.getByRole('complementary', { name: 'Testar o fluxo' })
  await painel.getByRole('button', { name: 'Testar', exact: true }).click()
  await expect(painel.locator('.estado-concluido')).toBeVisible()
})
