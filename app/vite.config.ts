import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// https://vite.dev/config/
export default defineConfig({
  base: '/',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Редактор draw.io (сервис drawio из docker-compose.yml) — на том же
      // адресе, что и платформа: встроенный редактор открывается фреймом.
      '/drawio': {
        target: 'http://localhost:8081',
        rewrite: (path) => path.replace(/^\/drawio/, ''),
        changeOrigin: true,
      },
      '/api': {
        // Порт тот же, что поднимает backend/start.sh. Пока они расходились,
        // каждый запрос к API молча падал, а выгрузка подменялась клиентской.
        target: 'http://localhost:8000',
        // Сохраняем адрес браузера: API проверяет Origin перед изменением документов.
        changeOrigin: false,
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
