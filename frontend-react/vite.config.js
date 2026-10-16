import path from 'path'
import { fileURLToPath } from 'url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const __dirname = path.dirname(fileURLToPath(import.meta.url))

// Built output is served by the FastAPI backend from /static (see backend/floodnet/config.py::FRONTEND_DIR
// and backend/floodnet/api/main.py's StaticFiles mount) -- base must match that mount prefix so the built
// index.html's asset URLs resolve. In dev, the Vite dev server proxies /api/* to the backend (see below) so
// `npm run dev` talks to a locally running `uvicorn floodnet.api.main:app --port 8000`.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  // '/static/' when FastAPI serves the build (default); VITE_BASE=/ for a static host such as Vercel.
  base: process.env.VITE_BASE || '/static/',
  appType: 'spa',     // enables historyApiFallback for pushState routes in dev
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
