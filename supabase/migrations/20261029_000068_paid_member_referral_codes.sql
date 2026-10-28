-- Paid-member referral codes can be configured by administrators.  Eligibility
-- is based on a successful subscription or 7-generation-pack purchase, not on
-- a browser-controlled entitlement value.  These rewards can coexist with
-- normal referral rewards; normal codes keep their sequential-use rule.

alter table public.referral_codes
  add column if not exists requires_paid_membership boolean not null default false;

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

  -- Serialize eligibility, capacity and per-member redemption checks.
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 481516));
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

  if v_code.requires_paid_membership and not exists (
    select 1
    from public.commerce_orders o
    join public.commerce_products p on p.code = o.product_code
    where o.user_id = v_user
      and o.status = 'paid'
      and p.kind in ('subscription', 'credit_pack')
  ) then
    raise exception 'referral_paid_member_required' using errcode = '42501';
  end if;

  -- Normal codes stay sequential. Paid-member-only codes are deliberately
  -- independent, so an eligible purchaser may combine them with normal codes.
  if not v_code.requires_paid_membership and exists (
    select 1
    from public.image_credit_grants g
    join public.referral_code_redemptions r on r.credit_grant_id = g.id
    join public.referral_codes prior_code on prior_code.id = r.code_id
    where g.user_id = v_user
      and g.source_kind = 'referral'
      and g.starts_at <= now()
      and g.expires_at > now()
      and g.remaining_credits > 0
      and not prior_code.requires_paid_membership
  ) then
    raise exception 'referral_previous_reward_remaining' using errcode = '23514';
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

drop function if exists public.admin_create_referral_code(
  text, integer, integer, timestamptz, text
);

create or replace function public.admin_create_referral_code(
  p_code text,
  p_redemption_limit integer,
  p_reward_credits integer,
  p_expires_at timestamptz,
  p_note text,
  p_requires_paid_membership boolean
)
returns table (
  code_id uuid,
  code text,
  redemption_limit integer,
  reward_credits integer,
  expires_at timestamptz,
  requires_paid_membership boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_actor uuid := auth.uid();
  v_code text := upper(trim(coalesce(p_code, '')));
  v_note text := trim(coalesce(p_note, ''));
  v_id uuid := gen_random_uuid();
  v_requires_paid_membership boolean := coalesce(p_requires_paid_membership, false);
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  if v_code !~ '^[A-Z0-9]{4,24}$' then
    raise exception 'invalid_referral_code_format' using errcode = '22023';
  end if;
  if p_redemption_limit not between 1 and 100000 then
    raise exception 'invalid_referral_limit' using errcode = '22023';
  end if;
  if p_reward_credits not between 1 and 100 then
    raise exception 'invalid_referral_reward' using errcode = '22023';
  end if;
  if char_length(v_note) > 160 then
    raise exception 'invalid_referral_note' using errcode = '22023';
  end if;
  if p_expires_at is not null and p_expires_at <= now() then
    raise exception 'invalid_referral_expiry' using errcode = '22023';
  end if;

  insert into public.referral_codes (
    id, code, redemption_limit, reward_credits, expires_at, note,
    requires_paid_membership, created_by
  ) values (
    v_id, v_code, p_redemption_limit, p_reward_credits, p_expires_at, v_note,
    v_requires_paid_membership, v_actor
  );
  return query select v_id, v_code, p_redemption_limit, p_reward_credits,
    p_expires_at, v_requires_paid_membership;
end;
$$;

drop function if exists public.admin_list_referral_codes();
create function public.admin_list_referral_codes()
returns table (
  code_id uuid,
  code text,
  redemption_limit integer,
  reward_credits integer,
  redeemed_count integer,
  is_active boolean,
  expires_at timestamptz,
  created_at timestamptz,
  note text,
  requires_paid_membership boolean
)
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  return query
  select
    c.id, c.code, c.redemption_limit, c.reward_credits,
    count(r.id)::integer, c.is_active, c.expires_at, c.created_at, c.note,
    c.requires_paid_membership
  from public.referral_codes c
  left join public.referral_code_redemptions r on r.code_id = c.id
  group by c.id
  order by c.created_at desc;
end;
$$;

revoke all on function public.redeem_referral_code(text) from public, anon;
revoke all on function public.admin_create_referral_code(text, integer, integer, timestamptz, text, boolean)
  from public, anon;
revoke all on function public.admin_list_referral_codes() from public, anon;
grant execute on function public.redeem_referral_code(text) to authenticated;
grant execute on function public.admin_create_referral_code(text, integer, integer, timestamptz, text, boolean)
  to authenticated;
grant execute on function public.admin_list_referral_codes() to authenticated;

notify pgrst, 'reload schema';
