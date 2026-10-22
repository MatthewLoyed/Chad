-- Capability self-improvement: Accept -> propose_skill_write -> commit_skill_write.
--
-- Extends the suggestion lifecycle so an accepted SKILL suggestion can record the
-- outcome of actually building it (status lifecycle becomes:
-- pending -> shown -> accepted -> built | build_failed, or accepted -> dismissed).
-- See src/core/intelligence/capability_skill_builder.py for the propose/commit pair.

ALTER TABLE public.capability_suggestions
    DROP CONSTRAINT IF EXISTS capability_suggestions_status_check;

ALTER TABLE public.capability_suggestions
    ADD CONSTRAINT capability_suggestions_status_check
    CHECK (status IN ('pending', 'shown', 'accepted', 'dismissed', 'built', 'build_failed'));

ALTER TABLE public.capability_suggestions
    ADD COLUMN IF NOT EXISTS skill_name TEXT,
    ADD COLUMN IF NOT EXISTS written_files JSONB,
    ADD COLUMN IF NOT EXISTS built_at TIMESTAMPTZ;
