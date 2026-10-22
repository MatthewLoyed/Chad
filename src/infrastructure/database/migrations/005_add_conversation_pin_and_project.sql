-- Sidebar actions for conversations (issue #107)
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS project_path TEXT;

CREATE INDEX IF NOT EXISTS idx_conversations_pinned_updated
    ON conversations (is_pinned DESC, updated_at DESC);
