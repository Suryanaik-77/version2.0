import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 3000,
    allowedHosts: true,
    proxy: {
      // Dev proxy — routes frontend calls to local backend
      // Note: /api/* proxy removed — frontend never calls /api/*
      '/auth':       { target: 'http://localhost:8000', changeOrigin: true },
      '/sessions':   { target: 'http://localhost:8000', changeOrigin: true },
      '/admin/':     { target: 'http://localhost:8000', changeOrigin: true },
      '/reviewer/':  { target: 'http://localhost:8000', changeOrigin: true },
      '/integrity':  { target: 'http://localhost:8000', changeOrigin: true },
      '/health':     { target: 'http://localhost:8000', changeOrigin: true },
      '/ws':         { target: 'ws://localhost:8000', ws: true, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router-dom'],
          charts: ['recharts'],
        },
      },
    },
  },
})
