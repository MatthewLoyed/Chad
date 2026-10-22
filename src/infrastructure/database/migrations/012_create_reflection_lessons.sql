-- Reflection lessons review/approve queue (#194).
--
-- The heartbeat's propose-only reflection cycle (src/core/intelligence/reflection.py
-- propose_lessons()) used to only write a markdown report + a Telegram ping; admitting a
-- lesson required the CLI (`--write`). This table lets each admitted lesson / open question
-- be reviewed and approved/dismissed individually from the GUI, mirroring
-- capability_suggestions (008_create_capability_gap_tables.sql).
--
-- status lifecycle:
--   lesson:   pending -> applied | dismissed   (or straight to auto_applied if the heartbeat's
--             reflection.auto_write_confidence_floor admits it automatically)
--   question: pending -> answered | dismissed
CREATE TABLE IF NOT EXISTS public.reflection_lessons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL CHECK (kind IN ('lesson', 'question')),
    scope TEXT,                              -- 'user' | 'tools' (null until a question is answered)
    text TEXT NOT NULL,                      -- lesson text, or the question text
    why TEXT,                                -- question's stated uncertainty
    evidence TEXT,
    confidence DOUBLE PRECISION DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'shown', 'applied', 'auto_applied', 'dismissed', 'answered')),
    answer TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reflection_lessons_status
    ON public.reflection_lessons (status, created_at DESC);

ALTER TABLE public.reflection_lessons ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to reflection_lessons" ON public.reflection_lessons;
CREATE POLICY "Allow anon read/write to reflection_lessons"
ON public.reflection_lessons FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);
