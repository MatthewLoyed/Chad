/**
 * Pure helpers and types behind ClarifyingQuestionsCard.
 *
 * Split out of the component file so that module exports only components: mixing
 * component and non-component exports breaks React Fast Refresh for the whole file
 * (react-refresh/only-export-components), which means editing the card during dev
 * forces a full reload instead of a hot swap. The card re-exports the types so
 * existing `import type { ClarifyingQuestion } from './ClarifyingQuestionsCard'`
 * consumers keep working unchanged.
 */

export interface ClarifyingOption {
    label: string;
    description?: string;
}

export interface ClarifyingQuestion {
    question: string;
    header?: string;
    options: ClarifyingOption[];
    multiSelect?: boolean;
}

export interface ResolvedQaPair {
    question: string;
    answer: string;
}

export const OTHER_LABEL = 'Other';

export function isOtherLabel(label: string): boolean {
    const t = label.trim().toLowerCase();
    return t === 'other' || t.startsWith('other -') || t.startsWith('other—') || t === 'something else';
}

export function formatAnswerValue(val: string | string[] | undefined): string {
    if (val == null) return '';
    return Array.isArray(val) ? val.join(', ') : String(val);
}

export function formatAnswersSummary(
    questions: ClarifyingQuestion[],
    answers: Record<string, string | string[]>,
): string {
    // User-facing reply text: just the chosen answer(s), not "truncated header: answer".
    return questions
        .map((q) => formatAnswerValue(answers[q.question]))
        .filter((s) => s.length > 0)
        .join(' · ');
}

/** Parse stored decision JSON (or live answers) into question/answer pairs for the resolved UI. */
export function parseResolvedQaPairs(
    questions: ClarifyingQuestion[],
    choice?: string,
    liveAnswers?: Record<string, string | string[]>,
): ResolvedQaPair[] {
    let answers: Record<string, string | string[]> | undefined = liveAnswers;
    if (!answers && choice) {
        try {
            const parsed = JSON.parse(choice) as { answers?: Record<string, string | string[]> };
            if (parsed?.answers && typeof parsed.answers === 'object') {
                answers = parsed.answers;
            }
        } catch {
            // Plain label from legacy choice cards
        }
    }

    if (answers && questions.length > 0) {
        const pairs = questions
            .map((q) => ({
                question: q.question,
                answer: formatAnswerValue(answers![q.question]),
            }))
            .filter((p) => p.answer.length > 0);
        if (pairs.length > 0) return pairs;
    }

    if (choice?.trim()) {
        return [
            {
                question: questions[0]?.question ?? '',
                answer: choice.trim(),
            },
        ];
    }
    return [];
}
