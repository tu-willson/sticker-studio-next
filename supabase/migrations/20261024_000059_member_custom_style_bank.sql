-- Persist the member-owned 01–10 custom-style bank across sign-outs and
-- Streamlit session changes. Access is only available through auth.uid().

create table if not exists public.member_custom_style_banks (
  user_id uuid primary key references auth.users(id) on delete cascade,
  style_bank jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  constraint member_custom_style_banks_style_bank_object
    check (jsonb_typeof(style_bank) = 'object')
);

alter table public.member_custom_style_banks enable row level security;
revoke all on table public.member_custom_style_banks from anon, authenticated;

create or replace function public.get_my_custom_style_bank()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_bank jsonb;
begin
  if v_user is null then
    raise exception 'authentication_required' using errcode = '42501';
  end if;

  select style_bank into v_bank
  from public.member_custom_style_banks
  where user_id = v_user;

  return coalesce(v_bank, '{}'::jsonb);
end;
$$;

create or replace function public.save_my_custom_style_bank(p_style_bank jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_bank jsonb := coalesce(p_style_bank, '{}'::jsonb);
begin
  if v_user is null then
    raise exception 'authentication_required' using errcode = '42501';
  end if;
  if jsonb_typeof(v_bank) <> 'object' then
    raise exception 'invalid_custom_style_bank' using errcode = '22023';
  end if;

  insert into public.member_custom_style_banks (user_id, style_bank, updated_at)
  values (v_user, v_bank, now())
  on conflict (user_id) do update
  set style_bank = excluded.style_bank,
      updated_at = excluded.updated_at;

  return v_bank;
end;
$$;

revoke all on function public.get_my_custom_style_bank() from public, anon;
revoke all on function public.save_my_custom_style_bank(jsonb) from public, anon;
grant execute on function public.get_my_custom_style_bank() to authenticated;
grant execute on function public.save_my_custom_style_bank(jsonb) to authenticated;

notify pgrst, 'reload schema';
