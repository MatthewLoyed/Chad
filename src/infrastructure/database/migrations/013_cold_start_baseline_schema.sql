-- Cold-start baseline schema (issue #403).
--
-- Migrations 001-012 only ever captured a subset of the tables Chad actually
-- uses — persona_config, conversations, messages, memories, conversation_history,
-- system_state, skills, artifacts, and the Telegram/agent-task tables were all
-- created by hand against the maintainer's Supabase project and never committed.
-- A fresh user pointing at an empty Supabase project got none of them, so
-- PromptRegistry.warmup() (persona_config) and the tool registry (tools) both
-- failed at boot. This migration creates every missing table plus the two RPC
-- functions (retrieve_relevant_tools, match_memories) in their CURRENT form —
-- the ones in 001/003 are stale; these definitions match what's live in prod.
--
-- Single-tenant by design: no owner_id / auth.users coupling. That's prep for a
-- future hosted multi-tenant tier (#214/#215, design-only, not wired into any
-- repository code — grep confirms zero references), not needed for a
-- self-hosted single-user install. RLS is enabled with a permissive policy for
-- consistency with 006-012; the Brain always connects with the service_role key
-- and bypasses RLS anyway.
--
-- Safe to run against an existing (Matthew's) project too — every statement is
-- idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS / CREATE OR REPLACE).

-- ── persona_config — the "Cloud Master" prompt store ──────────────────────────
CREATE TABLE IF NOT EXISTS public.persona_config (
    id TEXT NOT NULL,
    version INT NOT NULL DEFAULT 1,
    environment TEXT NOT NULL DEFAULT 'dev',
    scope TEXT NOT NULL DEFAULT 'always',
    content TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    changelog TEXT,
    updated_at TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id, version, environment)
);
CREATE INDEX IF NOT EXISTS idx_persona_config_id ON public.persona_config (id);

ALTER TABLE public.persona_config ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to persona_config" ON public.persona_config;
CREATE POLICY "Allow anon read/write to persona_config"
ON public.persona_config FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);

-- ── conversations / messages — chat history for the web/desktop UI ───────────
CREATE TABLE IF NOT EXISTS public.conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL DEFAULT 'New Chat',
    model TEXT,
    project_path TEXT,
    is_pinned BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conversations_pinned_updated
    ON public.conversations (is_pinned DESC, updated_at DESC);

ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to conversations" ON public.conversations;
CREATE POLICY "Allow anon read/write to conversations"
ON public.conversations FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);

CREATE TABLE IF NOT EXISTS public.messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    is_archived BOOLEAN DEFAULT false,
    compacted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON public.messages (conversation_id);

ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to messages" ON public.messages;
CREATE POLICY "Allow anon read/write to messages"
ON public.messages FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);

-- ── memories / conversation_history — long-term memory + legacy chat log ─────
CREATE TABLE IF NOT EXISTS public.memories (
    id BIGSERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    importance DOUBLE PRECISION DEFAULT 0.5,
    created_at TIMESTAMPTZ DEFAULT now(),
    last_accessed TIMESTAMPTZ DEFAULT now(),
    access_count INT DEFAULT 1,
    source TEXT DEFAULT 'user',
    embedding vector(768),
    metadata JSONB DEFAULT '{}'::jsonb,
    session_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_memories_session_key ON public.memories (session_key);

ALTER TABLE public.memories ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to memories" ON public.memories;
CREATE POLICY "Allow anon read/write to memories"
ON public.memories FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);

CREATE TABLE IF NOT EXISTS public.conversation_history (
    id BIGSERIAL PRIMARY KEY,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB,
    timestamp TIMESTAMPTZ DEFAULT now(),
    session_key TEXT
);

ALTER TABLE public.conversation_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to conversation_history" ON public.conversation_history;
CREATE POLICY "Allow anon read/write to conversation_history"
ON public.conversation_history FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);

-- ── system_state — small key/value scratch table used by a few subsystems ────
CREATE TABLE IF NOT EXISTS public.system_state (
    key TEXT PRIMARY KEY,
    value JSONB DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE public.system_state ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to system_state" ON public.system_state;
CREATE POLICY "Allow anon read/write to system_state"
ON public.system_state FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);

-- ── skills — SKILL.md registry (mirrors src/skills/*/SKILL.md) ───────────────
CREATE TABLE IF NOT EXISTS public.skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL,
    file_path TEXT NOT NULL,
    subagent TEXT,
    version TEXT DEFAULT '1.0.0',
    enabled BOOLEAN DEFAULT true,
    tags TEXT[] DEFAULT '{}'::text[],
    requires TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE public.skills ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to skills" ON public.skills;
CREATE POLICY "Allow anon read/write to skills"
ON public.skills FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);

-- ── artifacts — generated file/image tokens surfaced in the UI ───────────────
CREATE TABLE IF NOT EXISTS public.artifacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INT,
    storage_path TEXT,
    session_id TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE public.artifacts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to artifacts" ON public.artifacts;
CREATE POLICY "Allow anon read/write to artifacts"
ON public.artifacts FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);

-- ── Telegram integration (optional feature — safe to leave empty) ────────────
CREATE TABLE IF NOT EXISTS public.telegram_chats (
    chat_id BIGINT PRIMARY KEY,
    user_id BIGINT NOT NULL,
    session_key TEXT UNIQUE NOT NULL,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    last_message_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT true
);

ALTER TABLE public.telegram_chats ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to telegram_chats" ON public.telegram_chats;
CREATE POLICY "Allow anon read/write to telegram_chats"
ON public.telegram_chats FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);

CREATE TABLE IF NOT EXISTS public.telegram_user_links (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT UNIQUE NOT NULL,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE public.telegram_user_links ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to telegram_user_links" ON public.telegram_user_links;
CREATE POLICY "Allow anon read/write to telegram_user_links"
ON public.telegram_user_links FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);

-- ── Cron/heartbeat agent tasks (scheduling.* feature) ─────────────────────────
CREATE TABLE IF NOT EXISTS public.agent_tasks (
    id BIGSERIAL PRIMARY KEY,
    task_id TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    schedule TEXT,
    last_run TIMESTAMPTZ,
    next_run TIMESTAMPTZ,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE public.agent_tasks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to agent_tasks" ON public.agent_tasks;
CREATE POLICY "Allow anon read/write to agent_tasks"
ON public.agent_tasks FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);

CREATE TABLE IF NOT EXISTS public.task_executions (
    id BIGSERIAL PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES public.agent_tasks(task_id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    args JSONB,
    result JSONB,
    success BOOLEAN NOT NULL,
    duration DOUBLE PRECISION,
    trigger_source TEXT,
    error TEXT,
    timestamp TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE public.task_executions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow anon read/write to task_executions" ON public.task_executions;
CREATE POLICY "Allow anon read/write to task_executions"
ON public.task_executions FOR ALL TO anon, authenticated
USING (true) WITH CHECK (true);

-- ── tools: bring an 001-era table up to the columns retrieve_relevant_tools
--    (below) actually needs — OATS refinement, full-text search ─────────────
ALTER TABLE public.tools
    ADD COLUMN IF NOT EXISTS embedding_model TEXT,
    ADD COLUMN IF NOT EXISTS embedding_refined vector(768),
    ADD COLUMN IF NOT EXISTS oats_last_refined TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS oats_outcome_count INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS intent_preamble TEXT,
    ADD COLUMN IF NOT EXISTS description_hash TEXT;

ALTER TABLE public.tools
    ADD COLUMN IF NOT EXISTS fts tsvector
    GENERATED ALWAYS AS (
        to_tsvector(
            'english',
            COALESCE(name, '') || ' ' || COALESCE(intent_preamble, '') || ' ' || COALESCE(description, '')
        )
    ) STORED;

CREATE INDEX IF NOT EXISTS tools_fts_idx ON public.tools USING gin (fts);

-- ── RPC: retrieve_relevant_tools — CURRENT definition (001/003 are stale) ────
-- Hybrid retrieval: full-text (ts_rank_cd) + vector (embedding_refined, falling
-- back to embedding) fused with reciprocal-rank-fusion. Falls back to
-- vector-only when the sanitized query is empty.
DROP FUNCTION IF EXISTS public.retrieve_relevant_tools(vector, double precision, integer, text[]);

CREATE OR REPLACE FUNCTION public.retrieve_relevant_tools(
    query_text text,
    query_embedding vector,
    match_count integer DEFAULT 12,
    match_threshold double precision DEFAULT 0.0
)
RETURNS TABLE(name text, description text, schema jsonb, similarity double precision, raw_similarity double precision)
LANGUAGE plpgsql
STABLE
AS $function$
DECLARE
  sanitized_query text;
BEGIN
  sanitized_query := regexp_replace(trim(query_text), '[^a-zA-Z0-9\s]', '', 'g');
  sanitized_query := regexp_replace(trim(sanitized_query), '\s+', ' | ', 'g');

  IF sanitized_query = '' OR sanitized_query IS NULL THEN
    RETURN QUERY
    WITH vector_search AS (
      SELECT
        id,
        (1 - (COALESCE(embedding_refined, embedding) <=> query_embedding))::double precision AS cosine_similarity,
        ROW_NUMBER() OVER (
          ORDER BY COALESCE(embedding_refined, embedding) <=> query_embedding
        ) AS rn
      FROM tools
      ORDER BY COALESCE(embedding_refined, embedding) <=> query_embedding
      LIMIT 30
    ),
    rrf_fusion AS (
      SELECT
        v.id,
        (1.0 / (60 + v.rn))::double precision AS rrf_score,
        COALESCE(v.cosine_similarity, 0.0)::double precision AS raw_sim
      FROM vector_search v
    )
    SELECT
      t.name,
      t.description,
      t.schema,
      r.rrf_score::double precision AS similarity,
      r.raw_sim::double precision AS raw_similarity
    FROM rrf_fusion r
    JOIN tools t ON t.id = r.id
    ORDER BY r.rrf_score DESC
    LIMIT match_count;
  ELSE
    RETURN QUERY
    WITH keyword_search AS (
      SELECT
        id,
        ROW_NUMBER() OVER (
          ORDER BY ts_rank_cd(fts, to_tsquery('english', sanitized_query)) DESC
        ) AS rn
      FROM tools
      WHERE fts @@ to_tsquery('english', sanitized_query)
      LIMIT 30
    ),
    vector_search AS (
      SELECT
        id,
        (1 - (COALESCE(embedding_refined, embedding) <=> query_embedding))::double precision AS cosine_similarity,
        ROW_NUMBER() OVER (
          ORDER BY COALESCE(embedding_refined, embedding) <=> query_embedding
        ) AS rn
      FROM tools
      ORDER BY COALESCE(embedding_refined, embedding) <=> query_embedding
      LIMIT 30
    ),
    rrf_fusion AS (
      SELECT
        COALESCE(k.id, v.id) AS id,
        (COALESCE(1.0 / (60 + k.rn), 0.0) +
         COALESCE(1.0 / (60 + v.rn), 0.0))::double precision AS rrf_score,
        COALESCE(v.cosine_similarity, 0.0)::double precision AS raw_sim
      FROM keyword_search k
      FULL OUTER JOIN vector_search v ON k.id = v.id
    )
    SELECT
      t.name,
      t.description,
      t.schema,
      r.rrf_score::double precision AS similarity,
      r.raw_sim::double precision AS raw_similarity
    FROM rrf_fusion r
    JOIN tools t ON t.id = r.id
    ORDER BY r.rrf_score DESC
    LIMIT match_count;
  END IF;
END;
$function$;

-- ── RPC: match_memories — vector similarity search over memories ────────────
-- Session isolation mirrors MemoryRepository._build_session_filter (Python):
-- exact match OR legacy/global (NULL) OR same account component of a
-- "{scope}:{account}:{channel}:{peer}" session key.
CREATE OR REPLACE FUNCTION public.match_memories(
    query_embedding vector,
    match_threshold double precision,
    match_count integer,
    filter_session_key text DEFAULT NULL::text
)
RETURNS TABLE(
    id bigint, content text, category text, importance double precision,
    created_at timestamptz, last_accessed timestamptz, source text,
    session_key text, similarity double precision
)
LANGUAGE plpgsql
AS $function$
BEGIN
  RETURN QUERY
  SELECT
    m.id, m.content, m.category, m.importance, m.created_at, m.last_accessed,
    m.source, m.session_key,
    1 - (m.embedding <=> query_embedding) AS similarity
  FROM memories m
  WHERE (
      filter_session_key IS NULL
      OR m.session_key IS NULL
      OR m.session_key = filter_session_key
      OR (
        POSITION(':' IN filter_session_key) > 0
        AND POSITION(':' IN m.session_key) > 0
        AND split_part(m.session_key, ':', 2) = split_part(filter_session_key, ':', 2)
      )
    )
    AND 1 - (m.embedding <=> query_embedding) > match_threshold
  ORDER BY m.embedding <=> query_embedding
  LIMIT match_count;
END;
$function$;
