import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // Proxy Client Portal Gateway requests through Vite so the browser
    // never makes a cross-origin fetch to the gateway's self-signed
    // HTTPS endpoint. `secure: false` lets Node accept the cert.
    proxy: {
      '/v1/api': {
        target: 'https://localhost:5001',
        changeOrigin: true,
        secure: false,
      },
    },
  },
});
