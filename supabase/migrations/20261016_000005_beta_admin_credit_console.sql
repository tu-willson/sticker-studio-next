-- Sticker Studio Next: private Beta credit-administration console.
--
-- No browser role receives table-level admin privileges.  A member can become
-- an administrator only through a deliberate, server-side insert into
-- admin_users.  The app calls the functions below with the administrator's
-- normal Supabase access token; each credit change is recorded immutably.

create table public.admin_users (
  user_id uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);

create table public.credit_admin_adjustments (
  id uuid primary key default gen_random_uuid(),
  actor_user_id uuid not null references auth.users(id) on delete restrict,
  target_user_id uuid not null references auth.users(id) on delete cascade,
  delta integer not null check (delta between -50 and 50 and delta <> 0),
  note text not null check (char_length(note) between 3 and 160),
  created_at timestamptz not null default now()
);

create index credit_admin_adjustments_target_created_idx
  on public.credit_admin_adjustments (target_user_id, created_at desc);

alter table public.admin_users enable row level security;
alter table public.credit_admin_adjustments enable row level security;

revoke all on table public.admin_users, public.credit_admin_adjustments
  from anon, authenticated;

create or replace function public.is_beta_admin()
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  return auth.uid() is not null and exists (
    select 1 from public.admin_users where user_id = auth.uid()
  );
end;
$$;

create or replace function public.admin_list_members(p_search text default '')
returns table (
  user_id uuid,
  email text,
  display_name text,
  credit_balance integer,
  created_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_search text := lower(trim(coalesce(p_search, '')));
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;

  return query
  select
    p.id,
    p.email,
    p.display_name,
    coalesce(sum(l.delta), 0)::integer,
    p.created_at
  from public.profiles p
  left join public.credit_ledger l on l.user_id = p.id
  where v_search = ''
     or lower(p.email) like '%' || v_search || '%'
     or lower(p.display_name) like '%' || v_search || '%'
  group by p.id, p.email, p.display_name, p.created_at
  order by p.created_at desc
  limit 100;
end;
$$;

create or replace function public.admin_adjust_member_credits(
  p_user_id uuid,
  p_delta integer,
  p_note text
)
returns table (user_id uuid, credit_balance integer)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_actor uuid := auth.uid();
  v_note text := trim(coalesce(p_note, ''));
  v_balance integer;
  v_adjustment_id uuid := gen_random_uuid();
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  if p_user_id is null or p_delta is null or p_delta = 0 or p_delta not between -50 and 50 then
    raise exception 'invalid_credit_adjustment' using errcode = '22023';
  end if;
  if char_length(v_note) < 3 or char_length(v_note) > 160 then
    raise exception 'invalid_adjustment_note' using errcode = '22023';
  end if;

  -- Serialize this member's balance calculation with the normal reservation RPC.
  perform pg_advisory_xact_lock(hashtextextended(p_user_id::text, 0));

  if not exists (select 1 from public.profiles where id = p_user_id) then
    raise exception 'member_not_found' using errcode = 'P0002';
  end if;

  select coalesce(sum(delta), 0)::integer into v_balance
  from public.credit_ledger
  where user_id = p_user_id;

  if v_balance + p_delta < 0 then
    raise exception 'negative_credit_balance_not_allowed' using errcode = '22023';
  end if;

  insert into public.credit_admin_adjustments (
    id, actor_user_id, target_user_id, delta, note
  ) values (
    v_adjustment_id, v_actor, p_user_id, p_delta, v_note
  );

  insert into public.credit_ledger (user_id, delta, reason, reference_id)
  values (p_user_id, p_delta, 'admin_adjustment', v_adjustment_id);

  return query select p_user_id, v_balance + p_delta;
end;
$$;

revoke all on function public.is_beta_admin() from public, anon;
revoke all on function public.admin_list_members(text) from public, anon;
revoke all on function public.admin_adjust_member_credits(uuid, integer, text)
  from public, anon;
grant execute on function public.is_beta_admin() to authenticated;
grant execute on function public.admin_list_members(text) to authenticated;
grant execute on function public.admin_adjust_member_credits(uuid, integer, text)
  to authenticated;
