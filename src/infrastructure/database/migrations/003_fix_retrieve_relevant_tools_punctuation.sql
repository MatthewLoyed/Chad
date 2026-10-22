-- Migration: Fix retrieve_relevant_tools punctuation syntax and return raw cosine similarity
DROP FUNCTION IF EXISTS public.retrieve_relevant_tools(text, vector, integer, double precision);

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
  -- 1. Strip all non-alphanumeric/non-space characters
  sanitized_query := regexp_replace(trim(query_text), '[^a-zA-Z0-9\s]', '', 'g');
  -- 2. Compress multiple spaces and replace them with ' | ' for partial matching
  sanitized_query := regexp_replace(trim(sanitized_query), '\s+', ' | ', 'g');
  
  -- 3. If the sanitized query is empty, fallback to simple vector search only!
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
