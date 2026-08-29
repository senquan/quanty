import { defineConfig } from '@vben/vite-config';

import ElementPlus from 'unplugin-element-plus/vite';

export default defineConfig(async () => {
  return {
    application: {},
    vite: {
      plugins: [
        ElementPlus({
          format: 'esm',
        }),
      ],
      server: {
        proxy: {
          // 清洗服务相关请求统一经主后端网关（:8000）/api/v1/cleaner 转发至各 data-cleaner 实例，
          // 不再由前端直连 data-cleaner（避免服务地址/鉴权冲突）。其余 /api 一律走主后端。
          '/api': {
            changeOrigin: true,
            rewrite: (path) => path.replace(/^\/api/, ''),
            // mock代理目标地址
            target: 'http://localhost:8000/api',
            ws: true,
          },
        },
      },
    },
  };
});
