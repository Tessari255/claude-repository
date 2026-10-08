/// <reference types="vitest" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Em desenvolvimento a API roda em outra porta; o proxy mantém tudo na mesma origem.
    proxy: { '/api': { target: 'http://127.0.0.1:8000' } },
  },
  build: { outDir: 'dist', sourcemap: false },
  test: { include: ['src/**/*.test.ts'], environment: 'node' },
})
