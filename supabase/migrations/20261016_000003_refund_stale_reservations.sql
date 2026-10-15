-- A browser or deployment can stop after a credit is reserved but before the
-- image call returns.  The next reservation for the same member safely closes
-- and refunds any reservation that has been abandoned for at least 15 minutes.

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

  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 0));

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
