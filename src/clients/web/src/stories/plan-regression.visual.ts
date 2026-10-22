/**
 * Behavioral regression tests — plan card deduplication in ChatStream.
 *
 * Complements pixel snapshots in all-stories.visual.ts with DOM assertions
 * that catch duplicate PlanCard rendering (the original production bug).
 */
import { test, expect } from '@playwright/test';

const REGRESSION_STORIES = [
    {
        id: 'layout-chatstream--with-execution-plan',
        name: 'WithExecutionPlan',
        expectedPlanCards: 1,
    },
    {
        id: 'layout-chatstream--active-duplicate-plan-ids',
        name: 'ActiveDuplicatePlanIds',
        expectedPlanCards: 1,
    },
];

for (const story of REGRESSION_STORIES) {
    test(`plan regression: ${story.name} renders ${story.expectedPlanCards} PlanCard`, async ({ page }) => {
        await page.goto(`/iframe.html?id=${story.id}&viewMode=story`);
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(300);

        await expect(page.locator('.plan-card')).toHaveCount(story.expectedPlanCards);
    });
}

test('plan regression: duplicate wire events through reducer stay at one card', async ({ page }) => {
    await page.goto('/iframe.html?id=layout-chatstream--active-duplicate-plan-ids&viewMode=story');
    await page.waitForLoadState('networkidle');

    const count = await page.locator('.plan-card').count();
    expect(count).toBeLessThanOrEqual(1);
});
