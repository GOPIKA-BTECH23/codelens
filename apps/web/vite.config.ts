import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// In dev the browser only ever talks to the Vite server. Requests to /api/* are forwarded to
// the API with the /api prefix removed. This mirrors the production plan (a single origin via a
// Vercel rewrite), so cookies and CORS behave the same way in both places.
const apiTarget = process.env.API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: true, // listen on 0.0.0.0 so the Docker port mapping works
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
    // File events do not cross the Windows -> Linux bind mount, so compose enables polling.
    watch: process.env.VITE_USE_POLLING === 'true' ? { usePolling: true, interval: 300 } : {},
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
