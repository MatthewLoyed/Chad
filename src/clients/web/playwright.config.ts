import { defineConfig, devices } from '@playwright/test';

/**
 * Chad UI — Playwright Visual Regression Config
 *
 * Captures pixel-perfect screenshots of every Storybook story and diffs
 * them against committed baselines. Run `npm run test:visual` to compare,
 * `npm run test:visual:update` to accept new baselines.
 *
 * Baselines are stored in src/stories/__snapshots__ and committed to git.
 * GitHub will show a diff preview for any changed PNG.
 */
export default defineConfig({
    testDir: './src/stories',
    testMatch: '**/*.visual.ts',
    snapshotDir: './src/stories/__snapshots__',
    snapshotPathTemplate: '{snapshotDir}/{testFilePath}/{arg}{ext}',

    // Each test gets one retry on CI to handle flaky first-paint timing
    retries: process.env.CI ? 1 : 0,

    // Run story tests sequentially to avoid race conditions with Storybook
    workers: 1,

    reporter: [
        ['html', { outputFolder: 'playwright-report', open: 'never' }],
        ['list'],
    ],

    use: {
        // Storybook dev server URL
        baseURL: 'http://localhost:6006',

        // Screenshot settings — consistent viewport for pixel-perfect diffs
        viewport: { width: 1280, height: 900 },
        deviceScaleFactor: 1,

        // Disable animations so screenshots are deterministic
        reducedMotion: 'reduce',

        // Capture full-page screenshots by default
        screenshot: 'only-on-failure',

        // Trace on first retry for debugging
        trace: 'on-first-retry',
    },

    projects: [
        {
            name: 'chromium',
            use: { ...devices['Desktop Chrome'] },
        },
    ],

    // Start Storybook before running visual tests
    webServer: {
        command: 'npm run storybook -- --ci',
        url: 'http://localhost:6006',
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
    },
});
