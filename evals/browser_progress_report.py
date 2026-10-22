"""Summarize saved browser A/B trials without making another model call."""
import argparse
import json
import statistics
from pathlib import Path

from evals.browser_progress_fixtures import CASES


def describe(values):
    if not values:
        return 'unavailable'
    return f'{statistics.median(values):.1f} median ({min(values):.1f}–{max(values):.1f} range)'


def report(rows, repeats=5, cases=CASES):
    expected = len(cases) * 2 * repeats
    lines = ['# Browser controller comparison', '',
             f'Coverage: {len(rows)}/{expected} trials; target {repeats} repeats per case and variant.', '',
             'Fictional headless pages; production graph, executors and sanitizer; '
             'Bonsai 27B at temperature 0 with thinking disabled. '
             'Alternating variant order, fresh browser context and session per trial. '
             'See per-trial manifests for runtime settings, prompt and source hashes.', '']
    lines += ['The step limit is 10 (legacy tool turns; controller logical actions), '
              'with a 120-second trial timeout. The fixture transport uses a real '
              'headless page and a viewport-filtered snapshot adapter, rather than '
              'the signed-in Playwright MCP server. Supervisor handoffs and GUI '
              'events are checked separately by regression tests.', '']
    for variant in ('legacy', 'candidate'):
        selected = [r for r in rows if r['variant'] == variant]
        deliverables = [r for r in selected if r['case'] != 'missing']
        missing = [r for r in selected if r['case'] == 'missing']
        completed = [r for r in deliverables if r.get('completed')]
        lines += [f'## {variant}', '',
                  f'- Verified deliverables: {len(completed)}/{len(deliverables)}.',
                  f'- Correctly blocked missing target: {sum(bool(r.get("correctly_blocked")) for r in missing)}/{len(missing)}.',
                  f'- Detected false-success claims: {sum(bool(r.get("false_success")) for r in selected)}; '
                  f'errors/timeouts: {sum("error" in r for r in selected)}.',
                  f'- Logical actions, all measured trials: {describe([r["logical_actions"] for r in selected if "logical_actions" in r])}.',
                  f'- Raw MCP calls, all trials: {describe([r["mcp_calls"] for r in selected])}.',
                  f'- Unproductive cycles, all measured trials: {describe([r["unproductive_cycles"] for r in selected if "unproductive_cycles" in r])}.',
                  f'- Elapsed seconds, completed tasks: {describe([r["elapsed_s"] for r in completed])}.',
                  f'- Peak recorded input characters, approximate: {describe([r["peak_context_chars"] for r in selected if "peak_context_chars" in r])}.',
                  f'- Peak provider-reported prompt tokens per trial: {describe([max(u.get("prompt_tokens", 0) for u in r["usage"]) for r in selected if r.get("usage")])}.']
        for key in ('prompt_tokens', 'completion_tokens'):
            totals = [sum(u.get(key, 0) for u in r['usage']) for r in completed if r.get('usage')]
            lines.append(f'- {key} summed per completed task: {describe(totals)}.')
        if variant == 'candidate':
            controllers = [r['controller'] for r in selected if r.get('controller')]
            lines += [f'- Tasks entering recovery: {sum(p["recoveries"] > 0 for p in controllers)}/{len(controllers)}.',
                      f'- Completed tasks after recovery: {sum(bool(r.get("completed")) and bool((r.get("controller") or {}).get("recoveries")) for r in selected)}.',
                      f'- Incomplete deliverable outcomes: {sum((r.get("controller") or {}).get("status") == "incomplete" for r in deliverables)}.']
        lines += ['', 'Per-case verified deliverables (missing reports correct blocked outcomes):', '']
        for case in cases:
            trials = [r for r in selected if r['case'] == case]
            key = 'correctly_blocked' if case == 'missing' else 'completed'
            lines.append(f'- {case}: {sum(bool(r.get(key)) for r in trials)}/{len(trials)}; '
                         f'{describe([r["elapsed_s"] for r in trials])} seconds.')
        lines.append('')
    lines += ['## Designated looping cases', '']
    looping = {variant: [r for r in rows if r['variant'] == variant and r['case'] in ('nested', 'lazy')]
               for variant in ('legacy', 'candidate')}
    for variant, trials in looping.items():
        lines.append(f'- {variant}: {sum(bool(r.get("completed")) for r in trials)}/{len(trials)} verified; '
                     f'{describe([r["unproductive_cycles"] for r in trials if "unproductive_cycles" in r])} unproductive cycles.')
    measured = {variant: [r['unproductive_cycles'] for r in trials if 'unproductive_cycles' in r]
                for variant, trials in looping.items()}
    if all(measured.values()) and statistics.mean(measured['legacy']):
        reduction = 1 - statistics.mean(measured['candidate']) / statistics.mean(measured['legacy'])
        lines.append(f'- Reduction in mean unproductive cycles: {reduction:.1%}. '
                     'Assess alongside failures, not as a standalone success measure.')
    lines += ['', '## Limits and rollout decision', '',
              'The feature flag stays off pending review. Capture success is checked against '
              'the exact expected body, including Unicode and newlines. Generic completion '
              'quotes are supporting evidence, not a universal validator of website outcomes.', '',
              'An unproductive cycle has neither a previously unseen rendered observation '
              'nor a verified exact capture. Read-only inspection can legitimately count '
              'as unproductive under this conservative metric; geometry alone is not '
              'task success. Both variants use this same fixture-side evaluator. '
              'False-success detection on legacy prose is heuristic; failed '
              'deliverable checks remain failures regardless of wording.', '',
              'Recorded character counts include messages and recorded schema fields; '
              'the legacy recorder omits tool description text. Use provider-reported '
              'prompt tokens for cross-variant input comparisons. The controller also '
              'enforces its own character budget before dispatch.', '',
              'Short/chat/nested fixtures were used during development. Lazy/modal/stale/'
              'missing/distraction results are evaluated without tuning against their trial '
              'outputs. This small suite does not establish general real-site performance.', '',
              'The distraction case shares the chat layout and distractor controls at a '
              'different URL; it is a repeat under a changed destination, not an independent '
              'held-out website. LM Studio reported Bonsai Q1_0 quantization and a loaded '
              'context of 40,704 tokens for these runs.', '',
              'The signed-in Gemini → native Google Docs smoke test is separate and requires '
              'the intended conversation. No live account was accessed by this benchmark.', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--cases', nargs='+', choices=CASES, default=list(CASES))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report(json.loads(args.input.read_text(encoding='utf-8')), args.repeats, args.cases), encoding='utf-8')
