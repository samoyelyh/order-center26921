import path from "path"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// https://vite.dev/config/
export default defineConfig({
  // 使用 history 路由 + 嵌套路径（/materials/upload 等），资源必须用根绝对路径，
  // 否则在 SPA 回退下深层路由会按相对目录解析 ./assets/* 而 404。
  base: '/',
  plugins: [react()],
  server: {
    port: 3000,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
