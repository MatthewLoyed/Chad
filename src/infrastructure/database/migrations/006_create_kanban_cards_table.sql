-- Personal todo kanban board (single-user app — no per-user scoping needed)
CREATE TABLE IF NOT EXISTS public.kanban_cards (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    description TEXT,
    column_id TEXT NOT NULL DEFAULT 'backlog'
        CHECK (column_id IN ('backlog', 'todo', 'in_progress', 'review', 'done')),
    order_index INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kanban_cards_column_order
    ON public.kanban_cards (column_id, order_index);

ALTER TABLE public.kanban_cards ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow anon read/write to kanban_cards" ON public.kanban_cards;
CREATE POLICY "Allow anon read/write to kanban_cards"
ON public.kanban_cards
FOR ALL
TO anon, authenticated
USING (true)
WITH CHECK (true);
