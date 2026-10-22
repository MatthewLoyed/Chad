-- Migration: Add OATS tables and columns and update retrieve_relevant_tools RPC
-- 1. Alter tools table
ALTER TABLE public.tools 
ADD COLUMN IF NOT EXISTS embedding_refined vector(768),
ADD COLUMN IF NOT EXISTS oats_last_refined timestamptz,
ADD COLUMN IF NOT EXISTS oats_outcome_count int DEFAULT 0;

-- 2. Create tool outcome log table
CREATE TABLE IF NOT EXISTS public.tool_outcome_log (
    id BIGSERIAL PRIMARY KEY,
    tool_name TEXT NOT NULL,
    query_text TEXT NOT NULL,
    query_embedding vector(768),
    outcome TEXT NOT NULL CHECK (outcome IN ('success', 'failure')),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Enable RLS and add policies for tool_outcome_log
ALTER TABLE public.tool_outcome_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow anon read/write to tool_outcome_log" ON public.tool_outcome_log;
CREATE POLICY "Allow anon read/write to tool_outcome_log" 
ON public.tool_outcome_log 
FOR ALL 
TO anon, authenticated
USING (true) 
WITH CHECK (true);

-- 4. Re-create retrieve_relevant_tools function to support OATS
CREATE OR REPLACE FUNCTION public.retrieve_relevant_tools(
  query_text text, 
  query_embedding vector, 
  match_count integer DEFAULT 12, 
  match_threshold double precision DEFAULT 0.0
)
 RETURNS TABLE(name text, description text, schema jsonb, similarity double precision)
 LANGUAGE sql
 STABLE
AS $function$
  WITH keyword_search AS (
    SELECT
      id,
      ROW_NUMBER() OVER (
        ORDER BY ts_rank_cd(fts, to_tsquery('english', regexp_replace(trim(query_text), '\s+', ' | ', 'g'))) DESC
      ) AS rn
    FROM tools
    -- Use OR (|) to allow partial matches on key terms like 'Discord' or 'Gmail'
    WHERE fts @@ to_tsquery('english', regexp_replace(trim(query_text), '\s+', ' | ', 'g'))
    LIMIT 30
  ),
  vector_search AS (
    SELECT
      id,
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
      COALESCE(1.0 / (60 + k.rn), 0.0) +
      COALESCE(1.0 / (60 + v.rn), 0.0) AS rrf_score
    FROM keyword_search k
    FULL OUTER JOIN vector_search v ON k.id = v.id
  )
  SELECT
    t.name,
    t.description,
    t.schema,
    r.rrf_score AS similarity
  FROM rrf_fusion r
  JOIN tools t ON t.id = r.id
  ORDER BY r.rrf_score DESC
  LIMIT match_count;
$function$;
