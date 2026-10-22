import { defineConfig } from 'vitest/config';

export default defineConfig({
    test: {
        // Default to node; *.dom.test.tsx opt into jsdom via a per-file
        // `// @vitest-environment jsdom` pragma so the real-component DOM
        // harness can mount ChatStream while the fast store tests stay in node.
        environment: 'node',
        include: ['src/**/*.test.{ts,tsx}'],
        css: false,
    },
});
