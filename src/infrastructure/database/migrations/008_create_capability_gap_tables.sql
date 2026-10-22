-- Capability self-improvement (Phase 1: the "blocked" signal).
--
-- When Chad explicitly calls `search_for_tool` and the registry returns nothing,
-- that is his own deliberate admission of a missing capability (far higher precision
-- than inferring "no tools selected" — see docs/features/CAPABILITY_SELF_IMPROVEMENT.md).
-- We persist each such miss here so the signal survives short sessions, then the
-- analyzer clusters recurring misses and drafts a tool/skill suggestion once a gap
-- recurs enough times.

-- 1. Raw gap events. One row per `search_for_tool` miss (or weak match).
CREATE TABLE IF NOT EXISTS public.capability_gap_log (
    id BIGSERIAL PRIMARY KEY,
    query_text TEXT NOT NULL,              -- what Chad was looking for
    query_embedding vector(768),           -- nomic-embed-text-v2-moe (for clustering)
    found_count INT NOT NULL DEFAULT 0,    -- tools returned by search_for_tool (0 = hard miss)
    matched_tools JSONB DEFAULT '[]'::jsonb,-- names of weak matches, if any
    turn_id UUID,                          -- links back to the originating turn
    processed_at TIMESTAMPTZ,              -- set when the analyzer has folded it into a cluster
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_capability_gap_log_unprocessed
    ON public.capability_gap_log (created_at)
    WHERE processed_at IS NULL;

ALTER TABLE public.capability_gap_log ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to capability_gap_log" ON public.capability_gap_log;
CREATE POLICY "Allow anon read/write to capability_gap_log"
ON public.capability_gap_log FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);

-- 2. Drafted suggestions. One row per surfaced "want me to build X?" proposal.
-- status lifecycle: pending -> shown -> accepted | dismissed
CREATE TABLE IF NOT EXISTS public.capability_suggestions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL CHECK (kind IN ('tool', 'skill')),
    title TEXT NOT NULL,
    description TEXT NOT NULL,             -- what the proposed capability would do
    rationale TEXT,                        -- why Chad thinks it's worth building
    evidence JSONB DEFAULT '[]'::jsonb,    -- the gap queries that motivated it
    confidence DOUBLE PRECISION DEFAULT 0.0,
    cluster_size INT NOT NULL DEFAULT 0,   -- how many gap events backed this
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'shown', 'accepted', 'dismissed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_capability_suggestions_status
    ON public.capability_suggestions (status, created_at DESC);

ALTER TABLE public.capability_suggestions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to capability_suggestions" ON public.capability_suggestions;
CREATE POLICY "Allow anon read/write to capability_suggestions"
ON public.capability_suggestions FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);
