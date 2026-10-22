-- Migration: Create tools table and match_tools RPC for RAG
-- This script should be run in the Supabase SQL Editor.

-- 1. Enable the vector extension if not already enabled
create extension if not exists vector;

-- 2. Create the tools table
create table if not exists tools (
  id uuid primary key default gen_random_uuid(),
  name text unique not null,
  description text,
  schema jsonb,
  category text,
  intent_preamble text,
  description_hash text,
  embedding vector(768), -- Dimension for nomic-embed-text-v2-moe
  created_at timestamptz default now()
);

-- 3. Create a vector index for performance
create index on tools using ivfflat (embedding vector_cosine_ops)
with (lists = 100);

-- 4. Create the RPC function for vector similarity search
create or replace function retrieve_relevant_tools (
  query_embedding vector(768),
  match_threshold float,
  match_count int,
  exclude_names text[] default '{}'
)
returns table (
  name text,
  description text,
  schema jsonb,
  similarity float
)
language plpgsql
as $$
begin
  return query
  select
    t.name,
    t.description,
    t.schema,
    1 - (t.embedding <=> query_embedding) as similarity
  from tools t
  where 1 - (t.embedding <=> query_embedding) > match_threshold
    and not (t.name = any(exclude_names))
  order by t.embedding <=> query_embedding
  limit match_count;
end;
$$;
