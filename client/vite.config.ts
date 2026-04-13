import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The browser talks to the Client Portal Gateway directly (base_url in
// the YAML config points at https://localhost:5001). We don't proxy
// through Vite any more because session cookies are scoped to the
// gateway's origin — a proxied request from :5173 would arrive cookieless
// and always read as unauthenticated. The gateway's CORS config (see
// ibPortal/root/conf.yaml) has to name http://localhost:5173 explicitly
// and set allowCredentials: true for this to work.
export default defineConfig({
  plugins: [react()],
});
