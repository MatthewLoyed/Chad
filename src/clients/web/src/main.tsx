import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { initBrowserTransport } from './transport'

// Register the PWA service worker only in secure contexts (http/https) — never on
// file:// (pywebview desktop), where navigator.serviceWorker.register() throws.
if (import.meta.env.PROD && location.protocol !== 'file:') {
  const { registerSW } = await import('virtual:pwa-register')
  registerSW({ immediate: true })
}

// Install the browser-native transport (REST + WebSocket shim) BEFORE React mounts
// so the app's pywebview-API polling finds it immediately. No-op inside the desktop
// GUI, where pywebview injects its own window.pywebview bridge. Awaited so the
// desktop-vs-browser decision (which races pywebview's async injection) settles
// before App ever renders the browser-connect gate.
await initBrowserTransport()

// Dev-only: arm the golden-frame recorder before anything streams, so reproducing a
// GUI bug is "drive the scenario, then window.chadDownloadFrames('turn_x.jsonl')"
// instead of remembering a dynamic-import incantation — and, crucially, so capture is
// already running when the bug happens rather than being started after the fact.
// Stripped from production builds by the import.meta.env.DEV guard.
if (import.meta.env.DEV) {
  const { installFrameRecorder, exposeFrameRecorder } = await import('./testkit/frameRecorder')
  exposeFrameRecorder()
  installFrameRecorder()
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// Main-thread Task Dispatcher (Polling Interval)
// This allows background threads in Python to trigger GUI-thread actions (like file dialogs)
setInterval(() => {
  if (window.pywebview?.api?.poll_dialog_queue) {
    window.pywebview.api.poll_dialog_queue();
  }
}, 200);
