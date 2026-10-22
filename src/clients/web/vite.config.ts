import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    react(),
    // Installable PWA (#192). The same bundle is loaded two ways:
    //  - Desktop pywebview via file:// (base './') — NOT a secure context, so the
    //    service worker silently never registers there. That's intended; the
    //    desktop Limb doesn't need offline/install.
    //  - Phone/browser served by the Brain at http://<tailnet-ip>:8767 (or HTTPS
    //    via `tailscale serve`, #179) — registers the SW, becomes installable.
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.png', 'apple-touch-icon.png'],
      manifest: {
        name: 'Chad AI',
        short_name: 'Chad',
        description: 'Chad — your AI assistant',
        // Served from the Brain root mount, so the app lives at '/'.
        start_url: '/',
        scope: '/',
        display: 'standalone',
        orientation: 'portrait',
        background_color: '#0f1115',
        theme_color: '#0f1115',
        icons: [
          { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
          {
            src: 'maskable-icon-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'maskable',
          },
        ],
      },
      workbox: {
        // Precache the app shell (built JS/CSS/HTML + icons) for instant launch
        // and offline shell rendering.
        globPatterns: ['**/*.{js,css,html,png,svg,woff2}'],
        // Never let the SW serve the SPA fallback for the Brain's own endpoints:
        // live turns (/ws) and app commands (/api/command, /webhooks) must always
        // hit the network, never a cached shell. /files/{token} and /artifacts are
        // included for the same reason — an html artifact previewed in an iframe
        // (or downloaded) is a navigation-type request too, and without this the SW
        // silently substitutes the cached app shell for the artifact's real content
        // (issue #528: the artifact preview pane hangs on its loading skeleton
        // because it's actually loading Chad's own index.html, whose asset paths
        // then 404 under /files/assets/... instead of /assets/...).
        navigateFallback: 'index.html',
        navigateFallbackDenylist: [/^\/api\//, /^\/ws/, /^\/webhooks/, /^\/files\//, /^\/artifacts/],
        // No runtime caching of API/WS traffic — only the precached static shell
        // is served offline. Everything dynamic stays network-only by default.
        runtimeCaching: [],
        // Inject push-handler.js so the generated SW handles Web Push events.
        importScripts: ['/push-handler.js'],
      },
      // Disabled — we register manually in main.tsx behind a file:// guard so the
      // desktop pywebview (file://) never attempts SW registration (it throws there).
      injectRegister: null,
      devOptions: { enabled: false },
    }),
  ],
  server: {
    port: 5173,
    strictPort: true,
    // Bind to all interfaces so a phone/PWA on the same Tailscale tailnet can
    // load the client over the PC's tailnet IP (e.g. http://100.x.y.z:5173)
    // without passing `--host` every time. Desktop dev (localhost) is unaffected.
    host: true,
  },
  base: './', // Required for file:// protocol (pywebview)
  build: {
    outDir: 'dist', // Output to local dist for pywebview
    rollupOptions: {
      output: {
        // Split large, stable vendor libs into their own cached chunks so the
        // app chunk stays small and vendor code isn't re-downloaded on app changes.
        manualChunks: {
          'vendor-react': ['react', 'react-dom'],
          'vendor-markdown': ['react-markdown', 'remark-gfm'],
          'vendor-motion': ['framer-motion'],
          'vendor-flow': ['@xyflow/react'],
        },
      },
    },
  },
})
