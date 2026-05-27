import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],

  // Serve at /app when deployed behind FastAPI
  base: '/app',

  // Build output → web/static/dist/ so FastAPI can serve it
  build: {
    outDir: path.resolve(__dirname, '../web/static/dist'),
    emptyOutDir: true,
  },

  // Dev server: proxy /api calls to FastAPI backend on port 8001
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8001',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8001',
        ws: true,
      },
    },
  },

  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
})
