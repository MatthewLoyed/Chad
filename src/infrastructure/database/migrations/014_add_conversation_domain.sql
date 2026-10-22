-- Domain agents: scope a conversation to a user-created domain (Financial, Fitness, ...).
-- Mirrors project_path from 005 -- NULL means the general/untagged tier.
-- The `domains` table itself is deliberately NOT mirrored here: a domain row holds the
-- user's own persona prose and stays local-only per the local-first policy.
ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS domain TEXT;

CREATE INDEX IF NOT EXISTS idx_conversations_domain
    ON conversations (domain);
