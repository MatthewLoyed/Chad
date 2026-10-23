import { test, expect } from '@playwright/test';

for (const width of [1280, 390]) {
    test(`guided setup stays readable at ${width}px`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto('/iframe.html?id=setup-first-conversation--guided-setup&viewMode=story');
        await expect(page.getByRole('heading', { name: 'Welcome to Chad' })).toBeVisible();
        await page.getByRole('button', { name: 'Get started' }).click();
        await page.getByRole('button', { name: /LM Studio Recommended/ }).click();
        await expect(page.getByText('Step 2 of 4')).toBeVisible();
        await page.getByRole('button', { name: 'LM Studio is open — continue' }).click();
        await expect(page.getByRole('heading', { name: 'Find your first AI model' })).toBeFocused();
        await expect(page.getByLabel('Context tokens')).toBeHidden();
        await page.getByRole('button', { name: 'Find a model for me' }).click();
        await expect(page.getByRole('region', { name: 'Suggested model' })).toBeVisible();
        await expect(page.getByRole('heading', { name: 'Example Chat 8B' })).toBeVisible();
        await expect(page.getByRole('button', { name: 'Back', exact: true })).toBeInViewport();
        await expect(page.getByLabel('Server address')).toHaveCount(0);
        await page.screenshot({ path: test.info().outputPath(`recommendation-${width}.png`), fullPage: true });
        await page.getByText('Compare other models', { exact: true }).click();
        await expect(page.getByRole('button', { name: /Example Chat 32B/ })).toBeDisabled();
        await page.getByRole('button', { name: /Example Chat 4B.*download/ }).click();
        await expect(page.getByRole('heading', { name: 'Example Chat 4B' })).toBeVisible();
        await page.getByRole('button', { name: 'Download Example Chat 4B' }).click();
        await expect(page.getByRole('group', { name: 'Confirm setup operation' })).toBeVisible();
        await expect(page.getByRole('button', { name: 'Confirm download' })).toBeFocused();
        await expect(page.getByRole('button', { name: 'Confirm download' })).toBeInViewport();
        await page.getByRole('button', { name: 'Cancel', exact: true }).click();
        await page.getByRole('button', { name: 'I already have a model — continue' }).click();
        await expect(page.getByText('Step 4 of 4')).toBeVisible();
        await expect(page.getByLabel('API key (only if your server requires one)')).toBeHidden();
        await page.getByRole('button', { name: 'Check connection', exact: true }).click();
        await expect(page.getByRole('button', { name: 'Use this model and open Chat' })).toBeVisible();
        await page.getByRole('button', { name: 'Back', exact: true }).click();
        await expect(page.getByRole('heading', { name: 'Example Chat 4B' })).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
}
