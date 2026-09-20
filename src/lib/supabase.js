import { createClient } from '@supabase/supabase-js'

// Public anon key — safe to expose client-side, same as the original
// login.html / index.html. Move to import.meta.env if you'd rather not
// hardcode these (Vite exposes VITE_-prefixed env vars to the client).
const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL || 'https://itlxwaictkckmuxpxvxs.supabase.co'
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY || 'sb_publishable_4x4kQ2uzmSw_PMnRnYBdiA_CgmrFo74'

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY)
