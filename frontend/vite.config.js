import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  // Base relativa: el mismo build sirve en la raíz de un dominio propio
  // y bajo una subruta (p. ej. licdia.unlu.edu.ar/rag-unlu/) sin recompilar.
  base: './',
  plugins: [react()]
})
