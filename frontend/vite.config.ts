import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In dev, the FastAPI backend runs on :8000; in production it serves the built frontend itself.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
