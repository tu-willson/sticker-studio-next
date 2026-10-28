-- PAYUNi Edge Functions use the Supabase server-side key.  It bypasses RLS,
-- but PostgreSQL table grants still apply when the project uses secret keys.
grant select on table public.commerce_orders to service_role;
grant select on table public.profiles to service_role;
