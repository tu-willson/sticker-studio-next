-- PostgreSQL exposes RETURNS TABLE column names as PL/pgSQL variables.
-- Qualify every table column so usage_date cannot be ambiguous with the
-- function's usage_date output column (SQLSTATE 42702).

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
  v_entitlement_id uuid;
  v_entitlement_kind text;
  v_entitlement_expires_at timestamptz;
  v_limit integer := 2;
  v_used integer := 0;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  select e.id, e.kind, e.daily_copy_limit, e.expires_at
  into v_entitlement_id, v_entitlement_kind, v_limit, v_entitlement_expires_at
  from public.member_entitlements as e
  where e.user_id = v_user
    and e.starts_at <= now()
    and e.expires_at > now()
  order by e.daily_copy_limit desc, e.expires_at desc, e.created_at desc
  limit 1;

  if not found then
    v_limit := 2;
    v_entitlement_kind := 'free';
    v_entitlement_expires_at := null;
  end if;

  select count(*)::integer into v_used
  from public.copy_generation_usage as u
  where u.user_id = v_user
    and u.usage_date = v_today
    and u.status in ('reserved', 'succeeded');

  return query select
    v_today, v_limit, v_used, greatest(v_limit - v_used, 0),
    v_entitlement_kind, v_entitlement_expires_at;
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
  v_entitlement_id uuid;
  v_entitlement_kind text;
  v_entitlement_expires_at timestamptz;
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

  update public.copy_generation_usage as u
  set status = 'failed', completed_at = now(), failure_code = 'stale_timeout'
  where u.user_id = v_user
    and u.status = 'reserved'
    and u.created_at < now() - interval '10 minutes';

  select e.id, e.kind, e.daily_copy_limit, e.expires_at
  into v_entitlement_id, v_entitlement_kind, v_limit, v_entitlement_expires_at
  from public.member_entitlements as e
  where e.user_id = v_user
    and e.starts_at <= now()
    and e.expires_at > now()
  order by e.daily_copy_limit desc, e.expires_at desc, e.created_at desc
  limit 1;

  if not found then
    v_limit := 2;
    v_entitlement_kind := 'free';
    v_entitlement_expires_at := null;
  end if;

  select u.* into v_existing
  from public.copy_generation_usage as u
  where u.user_id = v_user and u.idempotency_key = p_idempotency_key;
  if found then
    select count(*)::integer into v_used
    from public.copy_generation_usage as u
    where u.user_id = v_user
      and u.usage_date = v_today
      and u.status in ('reserved', 'succeeded');
    return query select v_existing.id,
      (v_existing.status in ('reserved', 'succeeded')), v_today, v_limit,
      greatest(v_limit - v_used, 0),
      v_entitlement_kind, v_entitlement_expires_at;
    return;
  end if;

  select count(*)::integer into v_used
  from public.copy_generation_usage as u
  where u.user_id = v_user
    and u.usage_date = v_today
    and u.status in ('reserved', 'succeeded');
  if v_used >= v_limit then
    return query select null::uuid, false, v_today, v_limit, 0,
      v_entitlement_kind, v_entitlement_expires_at;
    return;
  end if;

  v_job_id := gen_random_uuid();
  insert into public.copy_generation_usage (
    id, user_id, idempotency_key, usage_date, entitlement_id,
    daily_limit_snapshot, status
  ) values (
    v_job_id, v_user, p_idempotency_key, v_today, v_entitlement_id,
    v_limit, 'reserved'
  );
  return query select v_job_id, true, v_today, v_limit, v_limit - v_used - 1,
    v_entitlement_kind, v_entitlement_expires_at;
end;
$$;

notify pgrst, 'reload schema';
