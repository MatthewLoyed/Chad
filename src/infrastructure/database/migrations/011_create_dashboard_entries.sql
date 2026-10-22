-- Dashboard "my life" feed (issue #161).
--
-- Replaces the static DASHBOARD.md file with a structured, Chad-authored feed.
-- Chad appends rows via the `update_dashboard` tool from any turn (heartbeat,
-- Gmail webhook investigation, normal chat, research). `pinned` rows are
-- always-on life widgets (e.g. wedding countdown); everything else is a
-- reverse-chronological card feed. `action` carries a single runnable step
-- ("click here for me to do this for you" cards) — dispatched by
-- src/core/intelligence/dashboard_feed.py.

CREATE TABLE IF NOT EXISTS public.dashboard_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL CHECK (kind IN ('thought', 'life', 'status', 'discovery', 'action')),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,    -- e.g. {source:'gmail', link, target_date}
    action JSONB,                          -- {type:'tool'|'fast_track', target, params, label} — only for kind='action'
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'done', 'dismissed', 'expired')),
    pinned BOOLEAN NOT NULL DEFAULT false, -- always-on life widget, exempt from the chronological feed cutoff
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dashboard_entries_feed
    ON public.dashboard_entries (status, pinned DESC, created_at DESC);

ALTER TABLE public.dashboard_entries ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to dashboard_entries" ON public.dashboard_entries;
CREATE POLICY "Allow anon read/write to dashboard_entries"
ON public.dashboard_entries FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);
