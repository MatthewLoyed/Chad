-- Standardize kanban_cards on the shared todo/in_progress/needs_review/done
-- status model (src/domain/work_status.py). Drops the backlog column (folded
-- into todo) and renames review -> needs_review.
ALTER TABLE public.kanban_cards DROP CONSTRAINT IF EXISTS kanban_cards_column_id_check;

UPDATE public.kanban_cards SET column_id = 'todo' WHERE column_id = 'backlog';
UPDATE public.kanban_cards SET column_id = 'needs_review' WHERE column_id = 'review';

ALTER TABLE public.kanban_cards ALTER COLUMN column_id SET DEFAULT 'todo';
ALTER TABLE public.kanban_cards ADD CONSTRAINT kanban_cards_column_id_check
    CHECK (column_id IN ('todo', 'in_progress', 'needs_review', 'done'));
