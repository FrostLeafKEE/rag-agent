import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      // 模板中的 /logo.svg（public 资源）在 vitest(node) 下无法转 file URL，显式指向实际文件
      '/logo.svg': fileURLToPath(new URL('./public/logo.svg', import.meta.url)),
      '/favicon.svg': fileURLToPath(new URL('./public/favicon.svg', import.meta.url)),
    },
  },
  test: {
    environment: 'happy-dom',
    globals: true,
  },
})
