-- Member Beta guardrails:
-- * an account can have only one active image generation across every tab;
-- * after a successful image, its next use waits 60 seconds;
-- * credits remain a non-renewing ledger balance (no daily reset is added).

create unique index generation_jobs_one_reserved_per_user_idx
  on public.generation_jobs (user_id)
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
  v_has_existing boolean := false;
  v_active_job_id uuid;
  v_last_success_at timestamptz;
  v_balance integer;
  v_job_id uuid;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  -- One advisory lock makes all account-level decisions atomic, including
  -- requests made from multiple browser tabs.
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 0));

  -- Refund a browser/server interruption only after it has been abandoned.
  with stale_jobs as (
    update public.generation_jobs
    set status = 'failed', completed_at = now(), failure_code = 'stale_timeout'
    where user_id = v_user
      and status = 'reserved'
      and created_at < now() - interval '15 minutes'
    returning id
  )
  insert into public.credit_ledger (user_id, delta, reason, reference_id)
  select v_user, 1, 'generation_refund', id
  from stale_jobs
  on conflict (user_id, reason, reference_id) do nothing;

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

  select id into v_active_job_id
  from public.generation_jobs
  where user_id = v_user and status = 'reserved'
  order by created_at desc
  limit 1;

  if v_active_job_id is not null then
    -- A non-empty job_id with granted=false tells the app that another tab is
    -- still working, rather than incorrectly presenting this as no credits.
    return query select v_active_job_id, false, v_balance;
    return;
  end if;

  select max(completed_at) into v_last_success_at
  from public.generation_jobs
  where user_id = v_user and status = 'succeeded';

  if v_last_success_at is not null
     and v_last_success_at > now() - interval '60 seconds' then
    return query select null::uuid, false, v_balance;
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
