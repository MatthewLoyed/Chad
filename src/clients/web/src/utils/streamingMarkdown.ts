/**
 * Streaming markdown is often incomplete mid-turn. react-markdown/remark will
 * treat an unclosed ``` fence as a code block spanning the rest of the document,
 * which makes list markers like `* Theme` render as literal asterisks inside a
 * gray <pre> — exactly the "weird bullets while thinking" artifact.
 *
 * Repair strategy for an odd fence count: drop the last fence-opener line from
 * the *preview* copy so trailing content parses as normal markdown. When the
 * matching closer eventually arrives, the count is even and the real code block
 * renders normally.
 */

/** Neutralize a dangling ``` opener so trailing content isn't trapped in <pre>. */
export function closeUnclosedCodeFences(text: string): string {
    if (!text.includes('```')) return text;
    const lines = text.split('\n');
    const fenceIdxs: number[] = [];
    lines.forEach((line, i) => {
        if (/^[ \t]{0,3}```/.test(line)) fenceIdxs.push(i);
    });
    if (fenceIdxs.length % 2 === 0) return text;
    const last = fenceIdxs[fenceIdxs.length - 1];
    lines.splice(last, 1);
    return lines.join('\n');
}

/**
 * Turn-start used to broadcast a literal "..." placeholder that got glued onto
 * the first real reasoning tokens ("...Aura farmers"). Strip only that prefix.
 */
export function stripLeadingPlaceholderEllipsis(text: string): string {
    return text.replace(/^\.\.\.(?=\S)/, '');
}

/** Apply stream-safe repairs before rendering thought markdown. */
export function repairStreamingMarkdown(text: string): string {
    if (!text) return text;
    return closeUnclosedCodeFences(stripLeadingPlaceholderEllipsis(text));
}
