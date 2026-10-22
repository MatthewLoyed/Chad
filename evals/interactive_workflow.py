r"""interactive_workflow.py — drive a full plan->approve->execute workflow, but stop and hand
control back instead of auto-answering ask_user_question with a canned default.

Built on evals.live.probe_workflow(interactive=True). Use this instead of the runner's
--workflow lane when a run might hit a clarifying question you (a human, or an agent
operating this eval) actually want to answer for real, rather than "use your best judgment".

Usage:
    # Start a run. Exits with a JSON status line: either "completed" (done) or
    # "awaiting_human" (paused on a real question — read the printed question and answer).
    $env:PYTHONPATH="."; .venv\Scripts\python.exe -m evals.interactive_workflow \
        --prompt "plan me a honeymoon to aruba..." --category planning

    # Resume after deciding an answer (conv_id + token are printed by the run above).
    $env:PYTHONPATH="."; .venv\Scripts\python.exe -m evals.interactive_workflow \
        --resume --conv-id eval_wf_ab12cd34 --token CHAD-A7X9 --answer "Mid-October, 7 nights, lace styles, private tours"

Repeat --resume until status is "completed" (or "stuck"/"error"). Plan-approval pauses
(the initial "here's the plan, proceed?") are auto-advanced automatically — you only get
handed control for genuine clarifying questions (ask_user_question and similar).
"""
import argparse
import asyncio
import json
import os
import sys

os.environ.setdefault("CHAD_SERVER_URL", "ws://localhost:8788/ws")

from evals.live import probe_workflow  # noqa: E402


def _print_pending(trace) -> None:
    print(json.dumps({
        "status": "awaiting_human",
        "conv_id": trace.session_id,
        "token": trace.pending_approval_token,
        "tool": trace.pending_approval_tool,
        "title": trace.pending_approval_title,
        "prompt": trace.pending_approval_prompt,
        "questions": trace.pending_approval_questions,
        "rounds_so_far": trace.rounds,
    }, indent=2, default=str))


def _print_final(trace) -> None:
    print(json.dumps({
        "status": "completed" if trace.completed else ("error" if trace.error else "stuck"),
        "conv_id": trace.session_id,
        "rounds": trace.rounds,
        "error": trace.error,
        "tool_names": trace.tool_names,
        "final_response": trace.final_response,
        "plan": trace.plan,
        "final_plan": trace.final_plan,
    }, indent=2, default=str))


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prompt", help="Prompt to start a new workflow with (ignored on --resume).")
    ap.add_argument("--category", default="planning", help="Turn category, e.g. 'planning' or 'agent'.")
    ap.add_argument("--conv-id", help="Conversation id (required for --resume; optional to pin a fresh run's id).")
    ap.add_argument("--resume", action="store_true", help="Resume a paused run instead of starting one.")
    ap.add_argument("--token", help="pending_approval_token to resolve (with --resume).")
    ap.add_argument("--answer", help="Answer text to send for the pending question (with --resume).")
    ap.add_argument("--max-rounds", type=int, default=12)
    args = ap.parse_args()

    if args.resume:
        if not (args.conv_id and args.token and args.answer is not None):
            ap.error("--resume requires --conv-id, --token, and --answer")
        trace = await probe_workflow(
            "", category=args.category, conv_id=args.conv_id, max_rounds=args.max_rounds,
            interactive=True, resume_token=args.token, resume_answer=args.answer,
        )
    else:
        if not args.prompt:
            ap.error("--prompt is required unless --resume")
        trace = await probe_workflow(
            args.prompt, category=args.category, conv_id=args.conv_id,
            max_rounds=args.max_rounds, interactive=True,
        )

    if trace.error and not trace.awaiting_human:
        _print_final(trace)
        sys.exit(1)
    if trace.awaiting_human:
        _print_pending(trace)
        sys.exit(2)   # distinct exit code: "paused, needs an answer" — not success, not failure
    _print_final(trace)
    sys.exit(0 if trace.completed else 1)


if __name__ == "__main__":
    asyncio.run(main())
