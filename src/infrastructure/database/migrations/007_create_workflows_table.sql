-- Manual-trigger workflows: ordered tool-call sequences the user defines and
-- can run with one click ("automations"). No scheduling/branching in v1.
CREATE TABLE IF NOT EXISTS public.workflows (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    steps JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE public.workflows ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow anon read/write to workflows" ON public.workflows;
CREATE POLICY "Allow anon read/write to workflows"
ON public.workflows
FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);
