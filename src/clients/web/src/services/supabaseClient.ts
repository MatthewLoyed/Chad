import { createClient } from '@supabase/supabase-js';

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL as string;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string;

/** True only when VITE_SUPABASE_URL is a real project URL, not a placeholder. */
export function isSupabaseConfigured(): boolean {
    return /^https:\/\/[a-z0-9]+\.supabase\.co/.test(supabaseUrl ?? '');
}

export const supabase = createClient(
    isSupabaseConfigured() ? supabaseUrl : 'https://placeholder.supabase.co',
    supabaseAnonKey || 'placeholder',
);
