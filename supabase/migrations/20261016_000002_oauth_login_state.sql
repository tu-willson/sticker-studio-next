-- Keeps the PKCE verifier only for the short period between a user leaving
-- Streamlit for Google and returning to the same Streamlit URL.  Streamlit
-- session memory is not durable across that browser redirect, so this state
-- must be stored server-side and consumed exactly once.

create table public.oauth_login_states (
  state_digest text primary key check (state_digest ~ '^[0-9a-f]{64}$'),
  code_verifier text not null check (char_length(code_verifier) between 43 and 128),
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

alter table public.oauth_login_states enable row level security;
revoke all on table public.oauth_login_states from anon, authenticated;

create or replace function public.create_oauth_login_state(
  p_state_digest text,
  p_code_verifier text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_state_digest !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid_login_state';
  end if;

  if char_length(p_code_verifier) < 43 or char_length(p_code_verifier) > 128 then
    raise exception 'invalid_code_verifier';
  end if;

  -- A small opportunistic cleanup avoids keeping abandoned login attempts.
  delete from public.oauth_login_states where expires_at < now();

  insert into public.oauth_login_states (state_digest, code_verifier, expires_at)
  values (p_state_digest, p_code_verifier, now() + interval '10 minutes');
end;
$$;

create or replace function public.consume_oauth_login_state(p_state_digest text)
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
  v_code_verifier text;
begin
  delete from public.oauth_login_states
  where state_digest = p_state_digest
    and expires_at >= now()
  returning code_verifier into v_code_verifier;

  if v_code_verifier is null then
    raise exception 'invalid_or_expired_login_state';
  end if;

  return v_code_verifier;
end;
$$;

revoke all on function public.create_oauth_login_state(text, text) from public, authenticated;
revoke all on function public.consume_oauth_login_state(text) from public, authenticated;
grant execute on function public.create_oauth_login_state(text, text) to anon;
grant execute on function public.consume_oauth_login_state(text) to anon;
