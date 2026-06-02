import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [react(), tailwindcss()],

  // Tauri dev server must bind to all interfaces on a known port
  server: {
    port: 1420,
    strictPort: true,
    host: '0.0.0.0',
  },

  // No base path — Tauri serves from the dist root
  base: '/',

  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // Tauri requires a target that Chromium can run
    target: ['es2021', 'chrome100', 'safari13'],
    minify: !process.env.TAURI_DEBUG,
    sourcemap: !!process.env.TAURI_DEBUG,
  },

  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      '@shared': path.resolve(__dirname, '../shared'),
    },
  },
})
