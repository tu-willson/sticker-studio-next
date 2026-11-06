-- Separate a point reservation from an actual AI-provider call.
--
-- ``reserved`` means the browser accepted the click but the image request has
-- not begun.  It can safely be cancelled immediately.  ``processing`` means
-- the server has started the provider request; it may only be recovered after
-- the existing five-minute timeout.  This prevents a refresh from refunding a
-- point while an image is still being generated.

alter table public.generation_jobs
  drop constraint if exists generation_jobs_status_check;

alter table public.generation_jobs
  drop constraint if exists generation_jobs_status_lifecycle_check;

alter table public.generation_jobs
  add constraint generation_jobs_status_lifecycle_check
  check (status in ('reserved', 'processing', 'succeeded', 'failed'));

-- Reservations created by the earlier app did not have an explicit start
-- marker.  Treat them conservatively as processing, never as immediately
-- cancellable work.  Stale ones remain recoverable by their existing age.
update public.generation_jobs
set status = 'processing'
where status = 'reserved';

create or replace function public.reserve_generation_credit(p_idempotency_key uuid)
returns table (job_id uuid, granted boolean, balance integer)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_existing public.generation_jobs%rowtype;
  v_active_job_id uuid;
  v_last_success_at timestamptz;
  v_grant public.image_credit_grants%rowtype;
  v_balance integer;
  v_job_id uuid;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_idempotency_key is null then
    raise exception 'invalid_idempotency_key' using errcode = '22023';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 0));

  -- A failed instance cannot leave either an unstarted reservation or an
  -- active provider call blocking this member forever.
  with stale as (
    update public.generation_jobs j
    set status = 'failed', completed_at = now(), failure_code = 'stale_timeout'
    where j.user_id = v_user
      and j.status in ('reserved', 'processing')
      and j.created_at < now() - interval '15 minutes'
    returning j.credit_grant_id
  ), refunds as (
    select credit_grant_id, count(*)::integer as amount
    from stale
    where credit_grant_id is not null
    group by credit_grant_id
  )
  update public.image_credit_grants g
  set remaining_credits = least(g.total_credits, g.remaining_credits + refunds.amount)
  from refunds
  where g.id = refunds.credit_grant_id;

  v_balance := public.get_my_credit_balance();
  select * into v_existing
  from public.generation_jobs j
  where j.user_id = v_user
    and j.idempotency_key = p_idempotency_key;
  if found then
    return query select v_existing.id,
      (v_existing.status in ('reserved', 'processing', 'succeeded')),
      v_balance;
    return;
  end if;

  select j.id into v_active_job_id
  from public.generation_jobs j
  where j.user_id = v_user
    and j.status in ('reserved', 'processing')
  order by j.created_at desc
  limit 1;
  if v_active_job_id is not null then
    return query select v_active_job_id, false, v_balance;
    return;
  end if;

  select max(j.completed_at) into v_last_success_at
  from public.generation_jobs j
  where j.user_id = v_user and j.status = 'succeeded';
  if v_last_success_at is not null
     and v_last_success_at > now() - interval '60 seconds' then
    return query select null::uuid, false, v_balance;
    return;
  end if;

  select * into v_grant
  from public.image_credit_grants g
  where g.user_id = v_user
    and g.starts_at <= now()
    and g.expires_at > now()
    and g.remaining_credits > 0
  order by case
      when g.source_kind in ('legacy', 'trial', 'admin') then 1
      when g.source_kind = 'referral' then 2
      when g.source_kind in ('subscription', 'credit_pack') then 3
      when g.source_kind = 'template_reward' then 4
      else 5
    end,
    g.expires_at asc,
    g.created_at asc
  limit 1
  for update;
  if not found then
    return query select null::uuid, false, 0;
    return;
  end if;

  v_job_id := gen_random_uuid();
  update public.image_credit_grants
  set remaining_credits = remaining_credits - 1
  where id = v_grant.id;
  insert into public.generation_jobs (
    id, user_id, idempotency_key, api_mode, status, credit_grant_id
  ) values (
    v_job_id, v_user, p_idempotency_key, 'site_credit', 'reserved', v_grant.id
  );
  return query select v_job_id, true, v_balance - 1;
end;
$$;


create or replace function public.start_generation(p_job_id uuid)
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
  if p_job_id is null then
    raise exception 'invalid_generation_job' using errcode = '22023';
  end if;

  -- Serialise with recovery so cancellation cannot race the provider call.
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 0));
  update public.generation_jobs
  set status = 'processing'
  where id = p_job_id
    and user_id = v_user
    and status = 'reserved';
  if found then
    return true;
  end if;

  select status into v_status
  from public.generation_jobs
  where id = p_job_id and user_id = v_user;
  return v_status = 'processing';
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
  where id = p_job_id
    and user_id = v_user
    and status in ('reserved', 'processing');
  if found then
    return true;
  end if;
  select status into v_status
  from public.generation_jobs
  where id = p_job_id and user_id = v_user;
  return v_status = 'succeeded';
end;
$$;


create or replace function public.fail_generation(
  p_job_id uuid,
  p_failure_code text default null
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_grant_id uuid;
  v_status text;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  update public.generation_jobs
  set status = 'failed',
      completed_at = now(),
      failure_code = nullif(left(coalesce(p_failure_code, ''), 80), '')
  where id = p_job_id
    and user_id = v_user
    and status in ('reserved', 'processing')
  returning credit_grant_id into v_grant_id;
  if found then
    if v_grant_id is not null then
      update public.image_credit_grants
      set remaining_credits = least(total_credits, remaining_credits + 1)
      where id = v_grant_id;
    end if;
    return true;
  end if;
  select status into v_status
  from public.generation_jobs
  where id = p_job_id and user_id = v_user;
  return v_status = 'failed';
end;
$$;


drop function if exists public.get_my_generation_recovery_status();

create function public.get_my_generation_recovery_status()
returns table (
  has_reserved boolean,
  can_cancel boolean,
  can_recover boolean,
  seconds_remaining integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_status text;
  v_created_at timestamptz;
  v_seconds integer;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  select j.status, j.created_at
    into v_status, v_created_at
  from public.generation_jobs j
  where j.user_id = v_user
    and j.status in ('reserved', 'processing')
  order by j.created_at desc
  limit 1;

  if not found then
    return query select false, false, false, 0;
    return;
  end if;

  if v_status = 'reserved' then
    return query select true, true, false, 0;
    return;
  end if;

  v_seconds := greatest(
    0,
    ceil(extract(epoch from ((v_created_at + interval '5 minutes') - now())))::integer
  );
  return query select true, false, v_seconds = 0, v_seconds;
end;
$$;


create or replace function public.recover_my_stale_generation()
returns table (
  recovered boolean,
  reason text,
  seconds_remaining integer,
  balance integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_job_id uuid;
  v_status text;
  v_created_at timestamptz;
  v_grant_id uuid;
  v_seconds integer := 0;
  v_failure_code text;
  v_reason text;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 0));
  select j.id, j.status, j.created_at, j.credit_grant_id
    into v_job_id, v_status, v_created_at, v_grant_id
  from public.generation_jobs j
  where j.user_id = v_user
    and j.status in ('reserved', 'processing')
  order by j.created_at desc
  limit 1
  for update;

  if not found then
    return query select false, 'no_active'::text, 0, public.get_my_credit_balance();
    return;
  end if;

  if v_status = 'reserved' then
    v_failure_code := 'user_cancelled_before_start';
    v_reason := 'cancelled_before_start';
  else
    v_seconds := greatest(
      0,
      ceil(extract(epoch from ((v_created_at + interval '5 minutes') - now())))::integer
    );
    if v_seconds > 0 then
      return query select false, 'too_recent'::text, v_seconds, public.get_my_credit_balance();
      return;
    end if;
    v_failure_code := 'user_timeout_recovery';
    v_reason := 'recovered';
  end if;

  update public.generation_jobs
  set status = 'failed',
      completed_at = now(),
      failure_code = v_failure_code
  where id = v_job_id
    and user_id = v_user
    and status = v_status
  returning credit_grant_id into v_grant_id;

  if not found then
    return query select false, 'no_active'::text, 0, public.get_my_credit_balance();
    return;
  end if;

  if v_grant_id is not null then
    update public.image_credit_grants
    set remaining_credits = least(total_credits, remaining_credits + 1)
    where id = v_grant_id;
  end if;

  return query select true, v_reason, 0, public.get_my_credit_balance();
end;
$$;


revoke all on function public.reserve_generation_credit(uuid) from public, anon;
revoke all on function public.start_generation(uuid) from public, anon;
revoke all on function public.complete_generation(uuid) from public, anon;
revoke all on function public.fail_generation(uuid, text) from public, anon;
revoke all on function public.get_my_generation_recovery_status() from public, anon;
revoke all on function public.recover_my_stale_generation() from public, anon;

grant execute on function public.reserve_generation_credit(uuid) to authenticated;
grant execute on function public.start_generation(uuid) to authenticated;
grant execute on function public.complete_generation(uuid) to authenticated;
grant execute on function public.fail_generation(uuid, text) to authenticated;
grant execute on function public.get_my_generation_recovery_status() to authenticated;
grant execute on function public.recover_my_stale_generation() to authenticated;

notify pgrst, 'reload schema';
