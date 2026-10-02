import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// 后端端口只有一个来源：仓库根 `.env` 里的 YTDL_API_PORT（与 backend/app/config.py 同源，见
// docs/development.md 的环境变量表）。以前这里硬编码 8000，而后端可以换端口 ——
// 开发模式下 /api 会**静默代理到另一个程序上**（本机 8000 是 IncrediBuild 的 Coordinator），
// 报出来的错跟真实病因毫无关系。见 ai/bug-fix/009。
//
// 不用 `__dirname`：本包是 ESM（package.json 的 "type": "module"），
// 且 Vite 会把本文件打包成 frontend/ 下的临时 .mjs，`..` 都指向仓库根。
const repoRoot = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig(({ mode }) => {
  const apiPort = loadEnv(mode, repoRoot, "YTDL_").YTDL_API_PORT || "8000";
  return {
    plugins: [react()],
    server: {
      proxy: {
        "/api": `http://127.0.0.1:${apiPort}`
      }
    },
    test: {
      setupFiles: "./src/test/setup.ts",
      // 默认 `css: false` 会把 CSS 换成空模块，连 `./styles.css?raw` 也一起吃掉；
      // 而「基类不得声明版式」这条不变式只能靠读样式源文来断言（jsdom 不做布局）。
      css: true
    }
  };
});
