-- Sticker Studio Next: membership beta foundation
--
-- This migration is for a NEW Supabase project only.  It intentionally does
-- not reference the legacy LINE Sticker Web project, its quota RPCs, or its
-- service-role key.
--
-- Security model:
-- * Auth owns identity (auth.users).
-- * A profile and a one-time +2 credit entry are created atomically on signup.
-- * Browser clients may read only their own rows.
-- * Credit-changing operations are security-definer RPCs with server-side
--   idempotency and never accept a user_id from the client.

create extension if not exists pgcrypto;

create table public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null default '',
  display_name text not null default '',
  plan_code text not null default 'free_beta'
    check (plan_code in ('free_beta')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.generation_jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  idempotency_key uuid not null,
  api_mode text not null check (api_mode in ('site_credit', 'own_key')),
  status text not null check (status in ('reserved', 'succeeded', 'failed')),
  failure_code text,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  unique (user_id, idempotency_key)
);

create table public.credit_ledger (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  delta integer not null check (delta <> 0),
  reason text not null check (reason in (
    'signup_bonus',
    'generation_reserve',
    'generation_refund',
    'admin_adjustment'
  )),
  reference_id uuid not null,
  created_at timestamptz not null default now(),
  unique (user_id, reason, reference_id)
);

create index generation_jobs_user_created_idx
  on public.generation_jobs (user_id, created_at desc);
create index credit_ledger_user_created_idx
  on public.credit_ledger (user_id, created_at desc);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger profiles_set_updated_at
before update on public.profiles
for each row execute function public.set_updated_at();

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, email, display_name)
  values (
    new.id,
    coalesce(new.email, ''),
    coalesce(new.raw_user_meta_data ->> 'full_name', new.raw_user_meta_data ->> 'name', '')
  )
  on conflict (id) do nothing;

  -- reference_id = user id makes the signup grant independently idempotent.
  insert into public.credit_ledger (user_id, delta, reason, reference_id)
  values (new.id, 2, 'signup_bonus', new.id)
  on conflict (user_id, reason, reference_id) do nothing;

  return new;
end;
$$;

create trigger on_auth_user_created
after insert on auth.users
for each row execute procedure public.handle_new_user();

alter table public.profiles enable row level security;
alter table public.generation_jobs enable row level security;
alter table public.credit_ledger enable row level security;

-- No browser role receives broad table-write privileges.  All authoritative
-- credit writes happen through the RPCs below.
revoke all on table public.profiles, public.generation_jobs, public.credit_ledger
  from anon, authenticated;
grant select on table public.profiles, public.generation_jobs, public.credit_ledger
  to authenticated;

create policy "profiles_select_own"
on public.profiles for select to authenticated
using (id = auth.uid());

create policy "generation_jobs_select_own"
on public.generation_jobs for select to authenticated
using (user_id = auth.uid());

create policy "credit_ledger_select_own"
on public.credit_ledger for select to authenticated
using (user_id = auth.uid());

create or replace function public.get_my_credit_balance()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  return coalesce((
    select sum(delta)::integer
    from public.credit_ledger
    where user_id = v_user
  ), 0);
end;
$$;

create or replace function public.reserve_generation_credit(p_idempotency_key uuid)
returns table (job_id uuid, granted boolean, balance integer)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_existing public.generation_jobs%rowtype;
  v_has_existing boolean := false;
  v_balance integer;
  v_job_id uuid;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  -- Serializes the check-and-reserve operation per member, so two browser
  -- clicks cannot both spend the same final credit.
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 0));

  select * into v_existing
  from public.generation_jobs
  where user_id = v_user and idempotency_key = p_idempotency_key;
  v_has_existing := found;

  select coalesce(sum(delta)::integer, 0) into v_balance
  from public.credit_ledger
  where user_id = v_user;

  if v_has_existing then
    return query select
      v_existing.id,
      (v_existing.status in ('reserved', 'succeeded')),
      v_balance;
    return;
  end if;

  if v_balance < 1 then
    return query select null::uuid, false, v_balance;
    return;
  end if;

  v_job_id := gen_random_uuid();

  insert into public.generation_jobs (id, user_id, idempotency_key, api_mode, status)
  values (v_job_id, v_user, p_idempotency_key, 'site_credit', 'reserved');

  insert into public.credit_ledger (user_id, delta, reason, reference_id)
  values (v_user, -1, 'generation_reserve', v_job_id);

  return query select v_job_id, true, v_balance - 1;
end;
$$;

create or replace function public.complete_generation(p_job_id uuid)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_status text;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  update public.generation_jobs
  set status = 'succeeded', completed_at = now(), failure_code = null
  where id = p_job_id and user_id = v_user and status = 'reserved';

  if found then
    return true;
  end if;

  select status into v_status
  from public.generation_jobs
  where id = p_job_id and user_id = v_user;

  return v_status = 'succeeded';
end;
$$;

create or replace function public.fail_generation(p_job_id uuid, p_failure_code text default null)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_status text;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  update public.generation_jobs
  set
    status = 'failed',
    completed_at = now(),
    failure_code = nullif(left(coalesce(p_failure_code, ''), 80), '')
  where id = p_job_id and user_id = v_user and status = 'reserved';

  if found then
    insert into public.credit_ledger (user_id, delta, reason, reference_id)
    values (v_user, 1, 'generation_refund', p_job_id)
    on conflict (user_id, reason, reference_id) do nothing;
    return true;
  end if;

  select status into v_status
  from public.generation_jobs
  where id = p_job_id and user_id = v_user;

  return v_status = 'failed';
end;
$$;

revoke all on function public.get_my_credit_balance() from public, anon;
revoke all on function public.reserve_generation_credit(uuid) from public, anon;
revoke all on function public.complete_generation(uuid) from public, anon;
revoke all on function public.fail_generation(uuid, text) from public, anon;
grant execute on function public.get_my_credit_balance() to authenticated;
grant execute on function public.reserve_generation_credit(uuid) to authenticated;
grant execute on function public.complete_generation(uuid) to authenticated;
grant execute on function public.fail_generation(uuid, text) to authenticated;
