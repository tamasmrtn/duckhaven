import path from 'path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      // monaco-yaml's worker imports Monaco's worker entry by its path before
      // 0.55, which monaco-editor's package exports no longer map.
      'monaco-editor/esm/vs/editor/editor.worker.js':
        'monaco-editor/editor/editor.worker.js',
    },
  },
  // The dev server pre-bundles what the app imports, but not what a worker
  // does. These are the YAML worker's, several of them CommonJS.
  optimizeDeps: {
    include: [
      '@vscode/l10n',
      'jsonc-parser',
      'path-browserify',
      'prettier/plugins/estree',
      'prettier/plugins/yaml',
      'prettier/standalone',
      'vscode-languageserver-textdocument',
      'vscode-languageserver-types',
      'vscode-uri',
      'yaml',
    ],
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
