-- Sticker Studio Next: plan entitlements, expiring credit packs, and daily
-- AI-copy usage.  This migration deliberately does not enable the copy
-- assistant yet; it establishes the authoritative server-side accounting
-- needed before any site OpenAI key is used for member copy requests.

create table public.member_entitlements (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  kind text not null check (kind in ('credit_pack', 'subscription')),
  daily_copy_limit integer not null check (daily_copy_limit between 1 and 100),
  starts_at timestamptz not null default now(),
  expires_at timestamptz not null,
  external_reference text unique,
  note text not null default '' check (char_length(note) <= 160),
  created_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  check (expires_at > starts_at)
);

create index member_entitlements_active_idx
  on public.member_entitlements (user_id, starts_at, expires_at, daily_copy_limit desc);

-- Credit-pack image points are kept as dated grants.  They do not alter the
-- legacy Beta credit_ledger yet, so existing two-credit testing remains
-- untouched until the paid image-credit checkout is implemented.
create table public.image_credit_grants (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  entitlement_id uuid references public.member_entitlements(id) on delete cascade,
  total_credits integer not null check (total_credits > 0 and total_credits <= 10000),
  starts_at timestamptz not null default now(),
  expires_at timestamptz not null,
  external_reference text unique,
  created_at timestamptz not null default now(),
  check (expires_at > starts_at)
);

create index image_credit_grants_active_idx
  on public.image_credit_grants (user_id, starts_at, expires_at);

-- One row is one attempted AI-copy request.  The local Taiwan date is captured
-- at reservation time, so a user's daily quota is atomic across browser tabs.
create table public.copy_generation_usage (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  idempotency_key uuid not null,
  usage_date date not null,
  entitlement_id uuid references public.member_entitlements(id) on delete set null,
  daily_limit_snapshot integer not null check (daily_limit_snapshot between 1 and 100),
  status text not null check (status in ('reserved', 'succeeded', 'failed')),
  failure_code text,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  unique (user_id, idempotency_key)
);

create index copy_generation_usage_daily_idx
  on public.copy_generation_usage (user_id, usage_date, created_at desc);
create unique index copy_generation_usage_one_reserved_per_user_idx
  on public.copy_generation_usage (user_id) where status = 'reserved';

alter table public.member_entitlements enable row level security;
alter table public.image_credit_grants enable row level security;
alter table public.copy_generation_usage enable row level security;

revoke all on table public.member_entitlements, public.image_credit_grants,
  public.copy_generation_usage from anon, authenticated;
grant select on table public.member_entitlements, public.image_credit_grants,
  public.copy_generation_usage to authenticated;

create policy "member_entitlements_select_own"
on public.member_entitlements for select to authenticated
using (user_id = (select auth.uid()));

create policy "image_credit_grants_select_own"
on public.image_credit_grants for select to authenticated
using (user_id = (select auth.uid()));

create policy "copy_generation_usage_select_own"
on public.copy_generation_usage for select to authenticated
using (user_id = (select auth.uid()));

create or replace function public.get_my_copy_usage_status()
returns table (
  usage_date date,
  daily_limit integer,
  used_count integer,
  remaining_count integer,
  entitlement_kind text,
  entitlement_expires_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_today date := (now() at time zone 'Asia/Taipei')::date;
  v_entitlement public.member_entitlements%rowtype;
  v_limit integer := 2;
  v_used integer := 0;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  select * into v_entitlement
  from public.member_entitlements
  where user_id = v_user
    and starts_at <= now()
    and expires_at > now()
  order by daily_copy_limit desc, expires_at desc, created_at desc
  limit 1;
  if found then
    v_limit := v_entitlement.daily_copy_limit;
  end if;

  select count(*)::integer into v_used
  from public.copy_generation_usage
  where user_id = v_user and usage_date = v_today
    and status in ('reserved', 'succeeded');

  return query select
    v_today, v_limit, v_used, greatest(v_limit - v_used, 0),
    case when v_entitlement.id is null then 'free' else v_entitlement.kind end,
    v_entitlement.expires_at;
end;
$$;

create or replace function public.reserve_copy_generation(p_idempotency_key uuid)
returns table (
  job_id uuid,
  granted boolean,
  usage_date date,
  daily_limit integer,
  remaining_count integer,
  entitlement_kind text,
  entitlement_expires_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_today date := (now() at time zone 'Asia/Taipei')::date;
  v_entitlement public.member_entitlements%rowtype;
  v_existing public.copy_generation_usage%rowtype;
  v_limit integer := 2;
  v_used integer := 0;
  v_job_id uuid;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_idempotency_key is null then
    raise exception 'invalid_idempotency_key' using errcode = '22023';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 2));

  update public.copy_generation_usage
  set status = 'failed', completed_at = now(), failure_code = 'stale_timeout'
  where user_id = v_user and status = 'reserved'
    and created_at < now() - interval '10 minutes';

  select * into v_entitlement
  from public.member_entitlements
  where user_id = v_user
    and starts_at <= now()
    and expires_at > now()
  order by daily_copy_limit desc, expires_at desc, created_at desc
  limit 1;
  if found then
    v_limit := v_entitlement.daily_copy_limit;
  end if;

  select * into v_existing
  from public.copy_generation_usage
  where user_id = v_user and idempotency_key = p_idempotency_key;
  if found then
    select count(*)::integer into v_used
    from public.copy_generation_usage
    where user_id = v_user and usage_date = v_today
      and status in ('reserved', 'succeeded');
    return query select v_existing.id,
      v_existing.status in ('reserved', 'succeeded'), v_today, v_limit,
      greatest(v_limit - v_used, 0),
      case when v_entitlement.id is null then 'free' else v_entitlement.kind end,
      v_entitlement.expires_at;
    return;
  end if;

  select count(*)::integer into v_used
  from public.copy_generation_usage
  where user_id = v_user and usage_date = v_today
    and status in ('reserved', 'succeeded');
  if v_used >= v_limit then
    return query select null::uuid, false, v_today, v_limit, 0,
      case when v_entitlement.id is null then 'free' else v_entitlement.kind end,
      v_entitlement.expires_at;
    return;
  end if;

  v_job_id := gen_random_uuid();
  insert into public.copy_generation_usage (
    id, user_id, idempotency_key, usage_date, entitlement_id,
    daily_limit_snapshot, status
  ) values (
    v_job_id, v_user, p_idempotency_key, v_today, v_entitlement.id,
    v_limit, 'reserved'
  );
  return query select v_job_id, true, v_today, v_limit, v_limit - v_used - 1,
    case when v_entitlement.id is null then 'free' else v_entitlement.kind end,
    v_entitlement.expires_at;
end;
$$;

create or replace function public.complete_copy_generation(p_job_id uuid)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_status text;
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  update public.copy_generation_usage
  set status = 'succeeded', completed_at = now(), failure_code = null
  where id = p_job_id and user_id = v_user and status = 'reserved';
  if found then return true; end if;
  select status into v_status from public.copy_generation_usage where id = p_job_id and user_id = v_user;
  return v_status = 'succeeded';
end;
$$;

create or replace function public.fail_copy_generation(p_job_id uuid, p_failure_code text default null)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_status text;
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  update public.copy_generation_usage
  set status = 'failed', completed_at = now(),
      failure_code = nullif(left(coalesce(p_failure_code, ''), 80), '')
  where id = p_job_id and user_id = v_user and status = 'reserved';
  if found then return true; end if;
  select status into v_status from public.copy_generation_usage where id = p_job_id and user_id = v_user;
  return v_status = 'failed';
end;
$$;

revoke all on function public.get_my_copy_usage_status() from public, anon;
revoke all on function public.reserve_copy_generation(uuid) from public, anon;
revoke all on function public.complete_copy_generation(uuid) from public, anon;
revoke all on function public.fail_copy_generation(uuid, text) from public, anon;
grant execute on function public.get_my_copy_usage_status() to authenticated;
grant execute on function public.reserve_copy_generation(uuid) to authenticated;
grant execute on function public.complete_copy_generation(uuid) to authenticated;
grant execute on function public.fail_copy_generation(uuid, text) to authenticated;
