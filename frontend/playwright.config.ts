import { defineConfig } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const aqui = path.dirname(fileURLToPath(import.meta.url))
const backend = path.resolve(aqui, '../backend')
const dados = path.resolve(aqui, 'test-results/dados-e2e')
const python = path.join(backend, '.venv/bin/python')

// Em ambientes sem `npx playwright install`, aponte para um Chromium existente:
//   PLAYWRIGHT_CHROMIUM_PATH=/caminho/para/chrome npm run e2e
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined

function servidor(porta: number, extra: Record<string, string> = {}) {
  return {
    command: `${python} -m uvicorn app.main:criar_app --factory --host 127.0.0.1 --port ${porta}`,
    cwd: backend,
    url: `http://127.0.0.1:${porta}/api/sistema`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      TRAMA_DATA_DIR: path.join(dados, String(porta)),
      TRAMA_SEED_EXAMPLES: '0',
      TRAMA_TIMEOUT_S: '10',
      ...extra,
    },
  }
}

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: { launchOptions: { executablePath }, viewport: { width: 1600, height: 950 }, locale: 'pt-BR', trace: 'retain-on-failure' },
  projects: [
    { name: 'com-docker', testIgnore: /sem-docker/, use: { baseURL: 'http://127.0.0.1:8123' } },
    { name: 'sem-docker', testMatch: /sem-docker/, use: { baseURL: 'http://127.0.0.1:8124' } },
  ],
  webServer: [
    servidor(8123),
    servidor(8124, { TRAMA_DOCKER_BIN: 'docker-que-nao-existe' }),
  ],
})
