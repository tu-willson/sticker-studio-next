-- Sticker Studio Next: explicit spend priority plus repeatable referral codes.
--
-- Spend order for every member-facing image-credit charge:
--   1. basic-member / trial / administrator adjustment credits
--   2. referral rewards
--   3. subscription and 7-credit-pack allowances
--   4. marketplace creator rewards
-- Referral rewards are repeatable with different codes, but another code
-- cannot be redeemed while an active referral-reward lot still has credits.

alter table public.referral_code_redemptions
  drop constraint if exists referral_code_redemptions_redeemer_user_id_key;

alter table public.referral_code_redemptions
  add constraint referral_code_redemptions_code_redeemer_key
  unique (code_id, redeemer_user_id);

create or replace function public.get_my_referral_redemption()
returns table (
  code text,
  reward_credits integer,
  redeemed_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
declare v_user uuid := auth.uid();
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  return query
  select c.code, r.reward_credits, r.redeemed_at
  from public.referral_code_redemptions r
  join public.referral_codes c on c.id = r.code_id
  where r.redeemer_user_id = v_user
  order by r.redeemed_at desc;
end;
$$;

create or replace function public.get_my_credit_balance_breakdown()
returns table (
  basic_member_credits integer,
  referral_credits integer,
  subscription_or_pack_credits integer,
  marketplace_creator_reward_credits integer,
  total_credits integer
)
language plpgsql
security definer
set search_path = public
as $$
declare v_user uuid := auth.uid();
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  return query
  select
    coalesce(sum(g.remaining_credits) filter (
      where g.source_kind in ('legacy', 'trial', 'admin')
    ), 0)::integer,
    coalesce(sum(g.remaining_credits) filter (
      where g.source_kind = 'referral'
    ), 0)::integer,
    coalesce(sum(g.remaining_credits) filter (
      where g.source_kind in ('subscription', 'credit_pack')
    ), 0)::integer,
    coalesce(sum(g.remaining_credits) filter (
      where g.source_kind = 'template_reward'
    ), 0)::integer,
    coalesce(sum(g.remaining_credits), 0)::integer
  from public.image_credit_grants g
  where g.user_id = v_user
    and g.starts_at <= now()
    and g.expires_at > now()
    and g.remaining_credits > 0;
end;
$$;

create or replace function public.redeem_referral_code(p_code text)
returns table (
  code text,
  awarded_credits integer,
  credit_balance integer,
  remaining_redemptions integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_code_text text := upper(trim(coalesce(p_code, '')));
  v_code public.referral_codes%rowtype;
  v_redemption_id uuid := gen_random_uuid();
  v_grant_id uuid := gen_random_uuid();
  v_redeemed_count integer := 0;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if v_code_text !~ '^[A-Z0-9]{4,24}$' then
    raise exception 'invalid_referral_code_format' using errcode = '22023';
  end if;

  -- Serialise a member's redemption and active-referral check.  A member may
  -- redeem another *different* code only after the previous referral reward
  -- has no active credits left.
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 481516));
  if exists (
    select 1
    from public.image_credit_grants g
    where g.user_id = v_user
      and g.source_kind = 'referral'
      and g.starts_at <= now()
      and g.expires_at > now()
      and g.remaining_credits > 0
  ) then
    raise exception 'referral_previous_reward_remaining' using errcode = '23514';
  end if;

  -- Locking the code serialises capacity and prevents a duplicate redemption.
  select * into v_code
  from public.referral_codes c
  where c.code = v_code_text
  for update;
  if not found then
    raise exception 'referral_code_not_found' using errcode = 'P0002';
  end if;
  if not v_code.is_active then
    raise exception 'referral_code_inactive' using errcode = '22023';
  end if;
  if v_code.expires_at is not null and v_code.expires_at <= now() then
    raise exception 'referral_code_expired' using errcode = '22023';
  end if;
  if exists (
    select 1 from public.referral_code_redemptions r
    where r.code_id = v_code.id and r.redeemer_user_id = v_user
  ) then
    raise exception 'referral_code_already_redeemed' using errcode = '23505';
  end if;

  select count(*)::integer into v_redeemed_count
  from public.referral_code_redemptions r
  where r.code_id = v_code.id;
  if v_redeemed_count >= v_code.redemption_limit then
    raise exception 'referral_code_limit_reached' using errcode = '22023';
  end if;

  insert into public.image_credit_grants (
    id, user_id, total_credits, remaining_credits, starts_at, expires_at,
    external_reference, source_kind
  ) values (
    v_grant_id, v_user, v_code.reward_credits, v_code.reward_credits,
    now(), now() + interval '60 days',
    'referral-redemption:' || v_redemption_id::text, 'referral'
  );

  insert into public.referral_code_redemptions (
    id, code_id, redeemer_user_id, reward_credits, credit_grant_id
  ) values (
    v_redemption_id, v_code.id, v_user, v_code.reward_credits, v_grant_id
  );

  return query select
    v_code.code,
    v_code.reward_credits,
    public.get_my_credit_balance(),
    greatest(0, v_code.redemption_limit - v_redeemed_count - 1);
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
  order by case
      when g.source_kind in ('legacy', 'trial', 'admin') then 1
      when g.source_kind = 'referral' then 2
      when g.source_kind in ('subscription', 'credit_pack') then 3
      when g.source_kind = 'template_reward' then 4
      else 5
    end,
    g.expires_at asc, g.created_at asc
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

create or replace function public.redeem_shared_template(
  p_template_id uuid,
  p_idempotency_key uuid
)
returns table (
  template_id uuid,
  title text,
  charged_cost integer,
  remaining_credits integer,
  template_snapshot jsonb,
  style_reference_path text,
  already_unlocked boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_rights record;
  v_template public.shared_templates%rowtype;
  v_existing public.shared_template_redemptions%rowtype;
  v_balance integer := 0;
  v_remaining integer := 0;
  v_grant record;
  v_charge integer := 0;
  v_shared_count integer := 0;
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  if p_template_id is null or p_idempotency_key is null then
    raise exception 'invalid_shared_template_redemption' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 53));
  select * into v_existing from public.shared_template_redemptions r
  where r.consumer_id = v_user and r.template_id = p_template_id for update;
  if found then
    return query select v_existing.template_id,
      (select t.title from public.shared_templates t where t.id = v_existing.template_id),
      0, public.get_my_credit_balance_for_user(v_user), v_existing.snapshot,
      v_existing.style_reference_path, true;
    return;
  end if;
  select * into v_rights from public.get_my_creator_reel_library_status();
  if not v_rights.marketplace_eligible then
    raise exception 'shared_templates_marketplace_required' using errcode = '42501';
  end if;
  select * into v_template from public.shared_templates t
  where t.id = p_template_id and t.status = 'published' for update;
  if not found then raise exception 'shared_template_not_available' using errcode = 'P0002'; end if;
  if v_template.owner_id <> v_user then
    select count(*)::integer into v_shared_count from public.creator_reels r
    where r.owner_id = v_user and r.status = 'collected';
    select v_shared_count + count(*)::integer into v_shared_count
    from public.shared_template_redemptions redemption
    join public.shared_templates template on template.id = redemption.template_id
    where redemption.consumer_id = v_user and template.owner_id <> v_user;
    if v_shared_count >= v_rights.collected_reel_limit then
      raise exception 'shared_template_collection_limit' using errcode = '23514';
    end if;
    v_charge := v_template.apply_cost;
    v_balance := public.get_my_credit_balance_for_user(v_user);
    if v_balance < v_charge then raise exception 'shared_template_insufficient_credits' using errcode = '22023'; end if;
    v_remaining := v_charge;
    for v_grant in
      select g.id, g.remaining_credits from public.image_credit_grants g
      where g.user_id = v_user and g.starts_at <= now() and g.expires_at > now()
        and g.remaining_credits > 0
      order by case
          when g.source_kind in ('legacy', 'trial', 'admin') then 1
          when g.source_kind = 'referral' then 2
          when g.source_kind in ('subscription', 'credit_pack') then 3
          when g.source_kind = 'template_reward' then 4
          else 5
        end,
        g.expires_at asc, g.created_at asc
      for update
    loop
      exit when v_remaining = 0;
      update public.image_credit_grants g
      set remaining_credits = g.remaining_credits - least(g.remaining_credits, v_remaining)
      where g.id = v_grant.id;
      v_remaining := v_remaining - least(v_grant.remaining_credits, v_remaining);
    end loop;
    insert into public.image_credit_grants (
      user_id, total_credits, remaining_credits, starts_at, expires_at,
      external_reference, source_kind
    ) values (
      v_template.owner_id, v_charge, v_charge, now(), now() + interval '90 days',
      'template-reward:' || p_template_id::text || ':' || v_user::text, 'template_reward'
    );
  end if;
  insert into public.shared_template_redemptions (
    template_id, consumer_id, idempotency_key, charged_cost, snapshot, style_reference_path
  ) values (
    v_template.id, v_user, p_idempotency_key, v_charge, v_template.snapshot, v_template.style_reference_path
  );
  return query select v_template.id, v_template.title, v_charge,
    public.get_my_credit_balance_for_user(v_user), v_template.snapshot,
    v_template.style_reference_path, false;
end;
$$;

revoke all on function public.get_my_credit_balance_breakdown() from public, anon;
grant execute on function public.get_my_credit_balance_breakdown() to authenticated;

notify pgrst, 'reload schema';
