import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

/**
 * The dev server proxies the API rather than expecting the backend on a fixed port.
 *
 * Without this the browser would call `http://localhost:8000` directly, which turns every CORS
 * mistake in the backend into a frontend failure and makes the frontend impossible to run
 * against anything other than a locally running server. Proxying keeps the app same-origin in
 * development, so what works in `npm run dev` is what works in a build served by the backend.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.OPTICODE_API ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
