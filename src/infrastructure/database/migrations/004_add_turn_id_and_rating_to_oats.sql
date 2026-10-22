-- Migration: Add turn_id and user_rating to tool_outcome_log
-- 1. Alter tool_outcome_log table to add turn_id (UUID) and user_rating (text)
ALTER TABLE public.tool_outcome_log 
ADD COLUMN IF NOT EXISTS turn_id UUID,
ADD COLUMN IF NOT EXISTS user_rating TEXT CHECK (user_rating IN ('positive', 'negative'));

-- 2. Create index on turn_id for query and update performance
CREATE INDEX IF NOT EXISTS idx_tool_outcome_log_turn_id ON public.tool_outcome_log(turn_id);
