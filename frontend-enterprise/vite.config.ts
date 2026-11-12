import path from 'path';
import tailwindcss from '@tailwindcss/vite';
import { loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import svgr from 'vite-plugin-svgr';
import { defineConfig } from 'vitest/config';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    plugins: [react(), tailwindcss(), svgr()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    base: '/',
    build: {
      rolldownOptions: {
        output: {
          codeSplitting: {
            groups: [
              {
                name: 'vendor-react',
                test: /node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler|@remix-run)[\\/]/,
                priority: 30,
              },
              {
                name: 'vendor-pinyin',
                test: /node_modules[\\/]pinyin-pro[\\/]/,
                priority: 30,
              },
              {
                name: 'vendor-radix',
                test: /node_modules[\\/](radix-ui|@radix-ui)[\\/]/,
                priority: 30,
              },
              {
                name: 'vendor-ui',
                test: /node_modules[\\/](lucide-react|sonner|qrcode|clsx|tailwind-merge|class-variance-authority)[\\/]/,
                priority: 30,
              },
              {
                name: 'vendor',
                test: /node_modules[\\/]/,
                priority: 20,
              },
            ],
          },
        },
      },
    },
    test: {
      setupFiles: ['./src/test/setup.ts'],
      environmentOptions: {
        jsdom: {
          url: 'http://localhost/',
        },
      },
    },
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: env.VITE_PROXY_TARGET || 'http://localhost:8000',
          changeOrigin: true,
        },
      },
    },
  };
});
