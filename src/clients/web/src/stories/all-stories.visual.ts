/**
 * Visual Regression Test Suite — Chad UI
 *
 * Screenshots every story in every component at consistent viewport/theme.
 * Run:
 *   npm run test:visual           — compare against baselines
 *   npm run test:visual:update    — accept new / changed screenshots as new baselines
 *
 * Baselines live in src/stories/__snapshots__ and are committed to git.
 * Any PNG that changes appears in GitHub PR diffs for instant visual review.
 */
import { test, expect } from '@playwright/test';

// ─── Story index ──────────────────────────────────────────────────────────────
// Each entry maps to a Storybook iframe URL:
// /iframe.html?id=<component-id>--<story-id>&viewMode=story
//
// The id is derived from the story's `title` and export name:
//   title: 'Data Display/PlanCard'  → data-display-plan-card
//   export PlanCard.AwaitingApproval → awaiting-approval

const STORIES: Array<{ id: string; name: string; waitMs?: number }> = [
    { id: 'setup-hardware-assessment--cpu-only', name: 'HardwareSetup/CPUOnly' },
    { id: 'setup-hardware-assessment--insufficient-memory', name: 'HardwareSetup/InsufficientMemory' },
    // ── PlanCard ──────────────────────────────────────────────────────────────
    { id: 'data-display-plancard--awaiting-approval', name: 'PlanCard/AwaitingApproval' },
    { id: 'data-display-plancard--in-progress', name: 'PlanCard/InProgress' },
    { id: 'data-display-plancard--all-completed', name: 'PlanCard/AllCompleted' },
    { id: 'data-display-plancard--with-failure', name: 'PlanCard/WithFailure' },
    { id: 'data-display-plancard--history-collapsed', name: 'PlanCard/HistoryCollapsed' },
    { id: 'data-display-plancard--long-plan', name: 'PlanCard/LongPlan' },
    { id: 'data-display-plancard--single-step', name: 'PlanCard/SingleStep' },
    { id: 'data-display-plancard--no-goal', name: 'PlanCard/NoGoal' },

    // ── MarkdownMessage ────────────────────────────────────────────────────────
    { id: 'data-display-markdownmessage--plain-text', name: 'MarkdownMessage/PlainText' },
    { id: 'data-display-markdownmessage--rich-markdown', name: 'MarkdownMessage/RichMarkdown' },
    { id: 'data-display-markdownmessage--with-code-block', name: 'MarkdownMessage/WithCodeBlock' },
    { id: 'data-display-markdownmessage--with-gfm-table', name: 'MarkdownMessage/WithGFMTable' },
    { id: 'data-display-markdownmessage--with-multiple-code-blocks', name: 'MarkdownMessage/MultipleCodeBlocks' },
    { id: 'data-display-markdownmessage--long-code-block', name: 'MarkdownMessage/LongCodeBlock' },

    // ── ThinkingWidget ─────────────────────────────────────────────────────────
    { id: 'data-display-thinkingwidget--active-with-thoughts', name: 'ThinkingWidget/ActiveWithThoughts', waitMs: 200 },
    { id: 'data-display-thinkingwidget--completed-closed', name: 'ThinkingWidget/CompletedClosed' },
    { id: 'data-display-thinkingwidget--deep-reasoning', name: 'ThinkingWidget/DeepReasoning' },
    { id: 'data-display-thinkingwidget--history-entry', name: 'ThinkingWidget/HistoryEntry' },
    { id: 'data-display-thinkingwidget--empty-active', name: 'ThinkingWidget/EmptyActive', waitMs: 200 },

    // ── ToolStackBubble ────────────────────────────────────────────────────────
    { id: 'data-display-toolstackbubble--single-filesystem', name: 'ToolStackBubble/SingleFilesystem' },
    { id: 'data-display-toolstackbubble--stacked-filesystem', name: 'ToolStackBubble/StackedFilesystem' },
    { id: 'data-display-toolstackbubble--stacked-browser', name: 'ToolStackBubble/StackedBrowser' },
    { id: 'data-display-toolstackbubble--single-pending', name: 'ToolStackBubble/SinglePending' },
    { id: 'data-display-toolstackbubble--mixed-status', name: 'ToolStackBubble/MixedStatus' },
    { id: 'data-display-toolstackbubble--delegation-stack', name: 'ToolStackBubble/DelegationStack' },
    { id: 'data-display-toolstackbubble--large-stack', name: 'ToolStackBubble/LargeStack' },

    // ── ArtifactCard ───────────────────────────────────────────────────────────
    { id: 'data-display-artifactcard--text-file', name: 'ArtifactCard/TextFile' },
    { id: 'data-display-artifactcard--python-script', name: 'ArtifactCard/PythonScript' },
    { id: 'data-display-artifactcard--spreadsheet-export', name: 'ArtifactCard/SpreadsheetExport' },
    { id: 'data-display-artifactcard--pdf-document', name: 'ArtifactCard/PDFDocument' },
    { id: 'data-display-artifactcard--long-filename', name: 'ArtifactCard/LongFilename' },

    // ── ChatStream ──────────────────────────────────────────────────────────────
    { id: 'layout-chatstream--empty', name: 'ChatStream/Empty' },
    { id: 'layout-chatstream--simple-conversation', name: 'ChatStream/SimpleConversation', waitMs: 300 },
    { id: 'layout-chatstream--with-execution-plan', name: 'ChatStream/WithExecutionPlan', waitMs: 300 },
    { id: 'layout-chatstream--with-tool-calls', name: 'ChatStream/WithToolCalls', waitMs: 300 },
    { id: 'layout-chatstream--active-thinking', name: 'ChatStream/ActiveThinking', waitMs: 300 },
];

// ─── Test runner ──────────────────────────────────────────────────────────────

for (const story of STORIES) {
    test(`visual: ${story.name}`, async ({ page }) => {
        await page.goto(`/iframe.html?id=${story.id}&viewMode=story`);

        // Wait for Storybook story to fully render
        await page.waitForLoadState('networkidle');

        // Extra wait for animated / async stories
        if (story.waitMs) {
            await page.waitForTimeout(story.waitMs);
        }

        // Disable CSS animations & transitions for deterministic screenshots
        await page.addStyleTag({
            content: `
                *, *::before, *::after {
                    animation-duration: 0s !important;
                    animation-delay: 0s !important;
                    transition-duration: 0s !important;
                    transition-delay: 0s !important;
                }
            `,
        });

        await expect(page).toHaveScreenshot(`${story.name.replace(/\//g, '_')}.png`, {
            // Allow 0.1% pixel diff for sub-pixel antialiasing differences
            maxDiffPixelRatio: 0.001,
            // Full page for scroll-able stories
            fullPage: true,
        });
    });
}
