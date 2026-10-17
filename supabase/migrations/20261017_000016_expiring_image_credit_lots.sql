-- Sticker Studio Next: spendable image-credit lots.
--
-- A lot is the authoritative balance unit.  It records the source, expiry,
-- and remaining amount so pack expiry and one-cycle subscription carryover can
-- be enforced without trusting a browser counter.

alter table public.image_credit_grants
  add column if not exists source_kind text not null default 'legacy'
    check (source_kind in ('legacy', 'trial', 'credit_pack', 'subscription', 'admin'));

alter table public.generation_jobs
  add column if not exists credit_grant_id uuid
    references public.image_credit_grants(id) on delete restrict;

create index if not exists generation_jobs_credit_grant_idx
  on public.generation_jobs (credit_grant_id)
  where credit_grant_id is not null;

-- Preserve every currently available Beta point exactly once before switching
-- balance reads to credit lots.  Historic ledger rows remain as audit data.
insert into public.image_credit_grants (
  user_id, total_credits, remaining_credits, starts_at, expires_at,
  external_reference, source_kind
)
select
  l.user_id,
  sum(l.delta)::integer,
  sum(l.delta)::integer,
  min(l.created_at),
  now() + interval '100 years',
  'legacy-beta-balance:' || l.user_id::text,
  'legacy'
from public.credit_ledger l
group by l.user_id
having sum(l.delta) > 0
on conflict (external_reference) do nothing;

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
  ) on conflict (id) do nothing;

  insert into public.image_credit_grants (
    user_id, total_credits, remaining_credits, starts_at, expires_at,
    external_reference, source_kind
  ) values (
    new.id, 2, 2, now(), now() + interval '365 days',
    'signup-trial:' || new.id::text, 'trial'
  ) on conflict (external_reference) do nothing;
  return new;
end;
$$;

create or replace function public.get_my_credit_balance()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare v_user uuid := auth.uid();
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  return coalesce((
    select sum(g.remaining_credits)::integer
    from public.image_credit_grants g
    where g.user_id = v_user
      and g.starts_at <= now() and g.expires_at > now()
      and g.remaining_credits > 0
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
  v_active_job_id uuid;
  v_last_success_at timestamptz;
  v_grant public.image_credit_grants%rowtype;
  v_balance integer;
  v_job_id uuid;
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  if p_idempotency_key is null then raise exception 'invalid_idempotency_key' using errcode = '22023'; end if;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 0));

  -- A stale reservation returns to the exact lot from which it was taken.
  with stale as (
    update public.generation_jobs j
    set status = 'failed', completed_at = now(), failure_code = 'stale_timeout'
    where j.user_id = v_user and j.status = 'reserved'
      and j.created_at < now() - interval '15 minutes'
    returning j.credit_grant_id
  ), refunds as (
    select credit_grant_id, count(*)::integer as amount
    from stale where credit_grant_id is not null group by credit_grant_id
  )
  update public.image_credit_grants g
  set remaining_credits = least(g.total_credits, g.remaining_credits + refunds.amount)
  from refunds where g.id = refunds.credit_grant_id;

  v_balance := public.get_my_credit_balance();
  select * into v_existing
  from public.generation_jobs j
  where j.user_id = v_user and j.idempotency_key = p_idempotency_key;
  if found then
    return query select v_existing.id, (v_existing.status in ('reserved', 'succeeded')), v_balance;
    return;
  end if;
  select j.id into v_active_job_id from public.generation_jobs j
  where j.user_id = v_user and j.status = 'reserved'
  order by j.created_at desc limit 1;
  if v_active_job_id is not null then
    return query select v_active_job_id, false, v_balance;
    return;
  end if;
  select max(j.completed_at) into v_last_success_at from public.generation_jobs j
  where j.user_id = v_user and j.status = 'succeeded';
  if v_last_success_at is not null and v_last_success_at > now() - interval '60 seconds' then
    return query select null::uuid, false, v_balance;
    return;
  end if;

  select * into v_grant
  from public.image_credit_grants g
  where g.user_id = v_user and g.starts_at <= now() and g.expires_at > now()
    and g.remaining_credits > 0
  order by g.expires_at asc, g.created_at asc
  limit 1 for update;
  if not found then
    return query select null::uuid, false, 0;
    return;
  end if;
  v_job_id := gen_random_uuid();
  update public.image_credit_grants
  set remaining_credits = remaining_credits - 1 where id = v_grant.id;
  insert into public.generation_jobs (
    id, user_id, idempotency_key, api_mode, status, credit_grant_id
  ) values (v_job_id, v_user, p_idempotency_key, 'site_credit', 'reserved', v_grant.id);
  return query select v_job_id, true, v_balance - 1;
end;
$$;

create or replace function public.complete_generation(p_job_id uuid)
returns boolean
language plpgsql security definer set search_path = public
as $$
declare v_user uuid := auth.uid(); v_status text;
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  update public.generation_jobs set status = 'succeeded', completed_at = now(), failure_code = null
  where id = p_job_id and user_id = v_user and status = 'reserved';
  if found then return true; end if;
  select status into v_status from public.generation_jobs where id = p_job_id and user_id = v_user;
  return v_status = 'succeeded';
end;
$$;

create or replace function public.fail_generation(p_job_id uuid, p_failure_code text default null)
returns boolean
language plpgsql security definer set search_path = public
as $$
declare v_user uuid := auth.uid(); v_grant_id uuid; v_status text;
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  update public.generation_jobs
  set status = 'failed', completed_at = now(), failure_code = nullif(left(coalesce(p_failure_code, ''), 80), '')
  where id = p_job_id and user_id = v_user and status = 'reserved'
  returning credit_grant_id into v_grant_id;
  if found then
    if v_grant_id is not null then
      update public.image_credit_grants
      set remaining_credits = least(total_credits, remaining_credits + 1)
      where id = v_grant_id;
    end if;
    return true;
  end if;
  select status into v_status from public.generation_jobs where id = p_job_id and user_id = v_user;
  return v_status = 'failed';
end;
$$;

-- Existing admin controls now adjust lots instead of the retired aggregate
-- balance.  A negative adjustment consumes the earliest-expiring lots first.
create or replace function public.admin_adjust_member_credits(
  p_user_id uuid, p_delta integer, p_note text
)
returns table (user_id uuid, credit_balance integer)
language plpgsql security definer set search_path = public
as $$
declare
  v_actor uuid := auth.uid(); v_note text := trim(coalesce(p_note, ''));
  v_remaining integer; v_grant record; v_adjustment_id uuid := gen_random_uuid();
begin
  if not public.is_beta_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  if p_user_id is null or p_delta is null or p_delta = 0 or p_delta not between -50 and 50 then
    raise exception 'invalid_credit_adjustment' using errcode = '22023'; end if;
  if char_length(v_note) not between 3 and 160 then raise exception 'invalid_adjustment_note' using errcode = '22023'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_user_id::text, 0));
  if not exists (select 1 from public.profiles where id = p_user_id) then raise exception 'member_not_found' using errcode = 'P0002'; end if;
  if p_delta < 0 and coalesce((select sum(remaining_credits) from public.image_credit_grants where user_id = p_user_id and starts_at <= now() and expires_at > now()), 0) < -p_delta then
    raise exception 'negative_credit_balance_not_allowed' using errcode = '22023'; end if;
  insert into public.credit_admin_adjustments (id, actor_user_id, target_user_id, delta, note)
  values (v_adjustment_id, v_actor, p_user_id, p_delta, v_note);
  if p_delta > 0 then
    insert into public.image_credit_grants (user_id, total_credits, remaining_credits, starts_at, expires_at, external_reference, source_kind)
    values (p_user_id, p_delta, p_delta, now(), now() + interval '365 days', 'admin-adjustment:' || v_adjustment_id::text, 'admin');
  else
    v_remaining := -p_delta;
    for v_grant in select id, remaining_credits from public.image_credit_grants
      where user_id = p_user_id and starts_at <= now() and expires_at > now() and remaining_credits > 0
      order by expires_at asc, created_at asc for update
    loop
      exit when v_remaining = 0;
      update public.image_credit_grants set remaining_credits = remaining_credits - least(remaining_credits, v_remaining)
      where id = v_grant.id;
      v_remaining := v_remaining - least(v_grant.remaining_credits, v_remaining);
    end loop;
  end if;
  return query select p_user_id, public.get_my_credit_balance_for_user(p_user_id);
end;
$$;

create or replace function public.get_my_credit_balance_for_user(p_user_id uuid)
returns integer language sql security definer set search_path = public as $$
  select coalesce(sum(g.remaining_credits), 0)::integer from public.image_credit_grants g
  where g.user_id = p_user_id and g.starts_at <= now() and g.expires_at > now();
$$;

create or replace function public.admin_list_members(p_search text default '')
returns table (user_id uuid, email text, display_name text, credit_balance integer, created_at timestamptz)
language plpgsql security definer set search_path = public
as $$
declare v_search text := lower(trim(coalesce(p_search, '')));
begin
  if not public.is_beta_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  return query select p.id, p.email, p.display_name,
    coalesce(sum(g.remaining_credits) filter (where g.starts_at <= now() and g.expires_at > now()), 0)::integer,
    p.created_at
  from public.profiles p left join public.image_credit_grants g on g.user_id = p.id
  where v_search = '' or lower(p.email) like '%' || v_search || '%' or lower(p.display_name) like '%' || v_search || '%'
  group by p.id, p.email, p.display_name, p.created_at order by p.created_at desc limit 100;
end;
$$;

revoke all on function public.get_my_credit_balance_for_user(uuid) from public, anon, authenticated;
revoke all on function public.get_my_credit_balance() from public, anon;
revoke all on function public.reserve_generation_credit(uuid) from public, anon;
revoke all on function public.complete_generation(uuid) from public, anon;
revoke all on function public.fail_generation(uuid, text) from public, anon;
grant execute on function public.get_my_credit_balance() to authenticated;
grant execute on function public.reserve_generation_credit(uuid) to authenticated;
grant execute on function public.complete_generation(uuid) to authenticated;
grant execute on function public.fail_generation(uuid, text) to authenticated;

notify pgrst, 'reload schema';
