"""Pure scoring/decision logic for GraphRAGClient.forget() (#658).

Split out of graph_rag_client.py, which owns the graph itself (load, seed,
write, persist, migrate) and was already at the module-size ceiling. This
module has no graph mutation and no I/O -- it only decides, given the graph's
current nodes, which single node (if any) a forget description should
invalidate.

Why a dedicated scorer: the previous implementation divided keyword overlap
by the query length alone, so a short description like "my email" could
out-score every node sharing just one or two words with it -- a few keywords
were enough to beat any candidate regardless of how much of *that node's*
content was unrelated. Symmetric Jaccard (intersection / union) fixes that,
and the decisive-margin check below stops the function from ever guessing
between two plausible candidates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# A Jaccard score below this is too thin to act on by itself, even with no
# competing candidate -- e.g. "my email" against a single email fact.
MIN_SCORE = 0.2
# If the runner-up scores within this margin of the best match, the request is
# ambiguous: touching either node risks invalidating the wrong fact, so neither
# is invalidated. Both thresholds gate the same "decisive" check -- anything
# short of decisive comes back as candidates instead of a guess.
MARGIN = 0.12


@dataclass
class ForgetCandidate:
    node_id: str
    score: float
    content: str


@dataclass
class ForgetDecision:
    invalidate: ForgetCandidate | None
    candidates: list[ForgetCandidate] = field(default_factory=list)

    @property
    def ambiguous(self) -> bool:
        return self.invalidate is None and bool(self.candidates)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def score_forget_candidates(
    fact_description: str, nodes: Iterable[tuple[str, dict]]
) -> ForgetDecision:
    """Score every valid node against `fact_description` and decide what to forget.

    `nodes` is `(node_id, data)` pairs as yielded by `graph.nodes(data=True)`.
    Invalid nodes are the caller's concern to filter, but this also skips them
    defensively since `valid` may be missing on older nodes.
    """
    keywords = _words(fact_description)
    if not keywords:
        return ForgetDecision(invalidate=None)

    scored: list[ForgetCandidate] = []
    for node_id, data in nodes:
        if not data.get("valid", True):
            continue
        if data.get("kind") == "entity":
            label = f"{data.get('name', '')} {node_id}"
            content = data.get("name") or node_id
        else:
            label = data.get("content") or ""
            content = data.get("content") or ""
        label_words = _words(label)
        if not label_words:
            continue
        union = keywords | label_words
        score = len(keywords & label_words) / len(union) if union else 0.0
        if score > 0:
            scored.append(ForgetCandidate(node_id, score, content))

    if not scored:
        return ForgetDecision(invalidate=None)

    scored.sort(key=lambda c: c.score, reverse=True)
    best, runner_up = scored[0], (scored[1].score if len(scored) > 1 else 0.0)
    decisive = best.score >= MIN_SCORE and (best.score - runner_up) >= MARGIN

    if decisive:
        return ForgetDecision(invalidate=best)

    near_miss = [c for c in scored if best.score - c.score < MARGIN]
    return ForgetDecision(invalidate=None, candidates=near_miss[:5])
