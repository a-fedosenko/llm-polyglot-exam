import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The built app is served by FastAPI from backend/static, so one process
  // serves the UI and the API in production and there is no CORS story to get
  // wrong. In development Vite proxies the API instead.
  build: { outDir: '../backend/static', emptyOutDir: true },
  server: {
    port: 5173,
    // Overridable because 8000 is a popular port: another project's container
    // holding it should mean `POLYGLOT_API=http://127.0.0.1:8010 npm run dev`,
    // not editing a committed file to develop.
    proxy: {
      '/api': {
        target: process.env.POLYGLOT_API ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
