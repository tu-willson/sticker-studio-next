-- The Streamlit sign-in button is rendered before the visitor clicks it. Keep
-- its random PKCE state available long enough for someone working in the
-- studio to authenticate without having to restart the Google flow.
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

  delete from public.oauth_login_states where expires_at < now();

  insert into public.oauth_login_states (state_digest, code_verifier, expires_at)
  values (p_state_digest, p_code_verifier, now() + interval '24 hours');
end;
$$;
