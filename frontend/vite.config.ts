import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/ws': {
        target: 'http://localhost:8000',
        ws: true,
      },
      '/health': 'http://localhost:8000',
      '/chat': 'http://localhost:8000',
      '/voice': 'http://localhost:8000',
      '/session': 'http://localhost:8000',
      '/leads': 'http://localhost:8000',
      '/ingest': 'http://localhost:8000',
      '/rag': 'http://localhost:8000',
      '/livekit': 'http://localhost:8000',
    },
  },
})
