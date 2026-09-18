// SPDX-FileCopyrightText: 2026 Jan-Wijbrand Kolman
// SPDX-License-Identifier: MIT
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [tailwindcss(), vue()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  build: {
    outDir: '../api/dist',
    emptyOutDir: true,
  },
})
