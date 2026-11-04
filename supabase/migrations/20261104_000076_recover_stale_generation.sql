-- Sticker Studio Next: a member may explicitly release only their own
-- generation reservation once it has been unresolved for at least five
-- minutes. This supports recovery after a browser refresh or a Cloud Run
-- instance change without allowing a live job to be cancelled accidentally.

create or replace function public.get_my_generation_recovery_status()
returns table (
  has_reserved boolean,
  can_recover boolean,
  seconds_remaining integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_created_at timestamptz;
  v_seconds integer;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  select j.created_at into v_created_at
  from public.generation_jobs j
  where j.user_id = v_user
    and j.status = 'reserved'
  order by j.created_at desc
  limit 1;

  if not found then
    return query select false, false, 0;
    return;
  end if;

  v_seconds := greatest(
    0,
    ceil(extract(epoch from ((v_created_at + interval '5 minutes') - now())))::integer
  );
  return query select true, v_seconds = 0, v_seconds;
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
  v_created_at timestamptz;
  v_grant_id uuid;
  v_seconds integer;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  -- Share the reservation lock so a new generation cannot reserve a point
  -- while this recovery is deciding whether the previous job is stale.
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 0));

  select j.id, j.created_at, j.credit_grant_id
    into v_job_id, v_created_at, v_grant_id
  from public.generation_jobs j
  where j.user_id = v_user
    and j.status = 'reserved'
  order by j.created_at desc
  limit 1
  for update;

  if not found then
    return query select false, 'no_active'::text, 0, public.get_my_credit_balance();
    return;
  end if;

  v_seconds := greatest(
    0,
    ceil(extract(epoch from ((v_created_at + interval '5 minutes') - now())))::integer
  );
  if v_seconds > 0 then
    return query select false, 'too_recent'::text, v_seconds, public.get_my_credit_balance();
    return;
  end if;

  update public.generation_jobs
  set status = 'failed',
      completed_at = now(),
      failure_code = 'user_timeout_recovery'
  where id = v_job_id
    and user_id = v_user
    and status = 'reserved'
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

  return query select true, 'recovered'::text, 0, public.get_my_credit_balance();
end;
$$;


revoke all on function public.get_my_generation_recovery_status() from public, anon;
revoke all on function public.recover_my_stale_generation() from public, anon;
grant execute on function public.get_my_generation_recovery_status() to authenticated;
grant execute on function public.recover_my_stale_generation() to authenticated;

notify pgrst, 'reload schema';
