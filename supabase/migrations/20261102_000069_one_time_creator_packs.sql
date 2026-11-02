-- Replace recurring subscriptions with one-time creator packs.
-- Every successful purchase receives its own 30-day entitlement and credit lot.
-- Purchases never extend or replace an earlier lot; credits are consumed from
-- the earliest-expiring eligible lot after free and referral credits.

update public.commerce_products
set display_name = case code
      when 'basic_monthly' then '基本創作包'
      when 'standard_monthly' then '標準創作包'
      when 'advanced_monthly' then '進階創作包'
      when 'credit_pack_7' then '7 次生成包'
      else display_name
    end,
    image_credits = case code
      when 'basic_monthly' then 20
      when 'standard_monthly' then 30
      when 'advanced_monthly' then 45
      when 'credit_pack_7' then 7
      else image_credits
    end,
    metadata = coalesce(metadata, '{}'::jsonb) ||
      '{"billing_model":"one_time_30_day_pack","auto_renew":false}'::jsonb,
    updated_at = now()
where code in ('basic_monthly', 'standard_monthly', 'advanced_monthly', 'credit_pack_7');

-- Paid creative-pack rights are determined by the highest still-valid tier.
-- The 7-credit refill remains a credit-only product: it does not raise a
-- member's AI Ideas, cloud library, reel, or marketplace tier.
create or replace function public.get_my_effective_entitlement()
returns table (
  entitlement_id uuid,
  product_code text,
  entitlement_kind text,
  daily_copy_limit integer,
  library_capacity integer,
  expires_at timestamptz
)
language plpgsql security definer set search_path = public
as $$
declare v_user uuid := auth.uid();
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;

  return query
  select e.id, e.product_code, e.kind, e.daily_copy_limit,
         coalesce(e.library_capacity, 2), e.expires_at
  from public.member_entitlements e
  where e.user_id = v_user
    and e.product_code in ('basic_monthly', 'standard_monthly', 'advanced_monthly')
    and e.starts_at <= now() and e.expires_at > now()
  order by case e.product_code
      when 'advanced_monthly' then 3
      when 'standard_monthly' then 2
      when 'basic_monthly' then 1
      else 0
    end desc,
    e.expires_at desc,
    e.created_at desc
  limit 1;
  if found then return; end if;

  return query select null::uuid, null::text, 'free'::text, 2, 2, null::timestamptz;
end;
$$;

-- Detail rows power the member-facing paid-credit expiry breakdown.
create or replace function public.get_my_active_paid_credit_lots()
returns table (
  product_code text,
  product_name text,
  remaining_credits integer,
  expires_at timestamptz
)
language sql stable security definer set search_path = public
as $$
  select g.product_code,
         coalesce(p.display_name, g.product_code) as product_name,
         g.remaining_credits,
         g.expires_at
  from public.image_credit_grants g
  left join public.commerce_products p on p.code = g.product_code
  where g.user_id = auth.uid()
    and g.source_kind in ('subscription', 'credit_pack')
    and g.starts_at <= now() and g.expires_at > now()
    and g.remaining_credits > 0
  order by g.expires_at asc, g.created_at asc;
$$;

-- All new NewebPay purchases are one-time MPG checkouts.  Existing periodic
-- profiles are intentionally not used for new orders.
create or replace function public.create_my_newebpay_checkout(
  p_product_code text,
  p_idempotency_key uuid
)
returns table (
  checkout_id uuid,
  order_id uuid,
  provider_order_reference text,
  product_name text,
  amount_twd integer,
  payment_flow text,
  expires_at timestamptz
)
language plpgsql security definer set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_product public.commerce_products%rowtype;
  v_order public.commerce_orders%rowtype;
  v_session public.newebpay_checkout_sessions%rowtype;
  v_order_id uuid := gen_random_uuid();
  v_checkout_id uuid := gen_random_uuid();
  v_reference text;
  v_expiry timestamptz := now() + interval '15 minutes';
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  if p_idempotency_key is null then raise exception 'invalid_checkout_idempotency_key' using errcode = '22023'; end if;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 65));

  select * into v_product from public.commerce_products
  where code = trim(coalesce(p_product_code, '')) and is_active;
  if not found then raise exception 'active_product_not_found' using errcode = 'P0002'; end if;

  select s.* into v_session
  from public.newebpay_checkout_sessions s
  join public.commerce_orders o on o.id = s.order_id
  where s.user_id = v_user and s.idempotency_key = p_idempotency_key
    and s.expires_at > now() and o.status = 'pending'
  limit 1;
  if found then
    select * into v_order from public.commerce_orders where id = v_session.order_id;
    return query select v_session.id, v_order.id, v_order.provider_order_reference,
      coalesce(v_order.product_snapshot ->> 'display_name', v_order.product_code),
      v_order.amount_twd, 'mpg'::text, v_session.expires_at;
    return;
  end if;

  v_reference := 'N' || to_char(clock_timestamp(), 'YYMMDDHH24MISSMS')
    || substr(replace(v_order_id::text, '-', ''), 1, 3);
  insert into public.commerce_orders (
    id, user_id, product_code, status, payment_provider, provider_order_reference,
    amount_twd, product_snapshot
  ) values (
    v_order_id, v_user, v_product.code, 'pending', 'newebpay_mpg', v_reference,
    v_product.price_twd,
    jsonb_build_object(
      'display_name', v_product.display_name, 'listed_price_twd', v_product.price_twd,
      'image_credits', v_product.image_credits, 'daily_copy_limit', v_product.daily_copy_limit,
      'library_capacity', v_product.library_capacity, 'credit_valid_days', 30,
      'billing_model', 'one_time_30_day_pack', 'payment_provider', 'newebpay'
    )
  );
  insert into public.newebpay_checkout_sessions (
    id, order_id, user_id, idempotency_key, payment_flow, expires_at
  ) values (v_checkout_id, v_order_id, v_user, p_idempotency_key, 'mpg', v_expiry);
  return query select v_checkout_id, v_order_id, v_reference,
    v_product.display_name, v_product.price_twd, 'mpg'::text, v_expiry;
end;
$$;

-- Verified one-time callbacks issue one independent 30-day entitlement/lot.
create or replace function public.finalize_newebpay_initial_payment(
  p_provider_order_reference text,
  p_provider_transaction_reference text,
  p_provider_period_reference text,
  p_paid_amount integer,
  p_payment_status text,
  p_payment_result jsonb default '{}'::jsonb
)
returns table (order_id uuid, order_status text, newly_paid boolean)
language plpgsql security definer set search_path = public
as $$
declare
  v_order public.commerce_orders%rowtype;
  v_product public.commerce_products%rowtype;
  v_entitlement_id uuid := gen_random_uuid();
  v_start timestamptz := now();
  v_end timestamptz := now() + interval '30 days';
  v_reference text := trim(coalesce(p_provider_order_reference, ''));
  v_transaction text := nullif(trim(coalesce(p_provider_transaction_reference, '')), '');
  v_status text := upper(trim(coalesce(p_payment_status, '')));
begin
  if v_reference = '' or p_paid_amount is null or p_paid_amount < 1 then
    raise exception 'newebpay_payment_reference_or_amount_invalid' using errcode = '22023';
  end if;
  select * into v_order from public.commerce_orders o
  where o.payment_provider in ('newebpay_mpg', 'newebpay_periodic')
    and o.provider_order_reference = v_reference for update;
  if not found then raise exception 'newebpay_order_not_found' using errcode = 'P0002'; end if;
  if v_order.status = 'paid' then return query select v_order.id, v_order.status, false; return; end if;
  if v_order.status <> 'pending' then return query select v_order.id, v_order.status, false; return; end if;
  if v_status <> 'SUCCESS' then
    update public.commerce_orders set status = 'failed', provider_transaction_reference = v_transaction,
      payment_result = coalesce(p_payment_result, '{}'::jsonb) where id = v_order.id;
    return query select v_order.id, 'failed'::text, false; return;
  end if;
  if p_paid_amount <> v_order.amount_twd then
    update public.commerce_orders set status = 'failed', provider_transaction_reference = v_transaction,
      payment_result = jsonb_set(coalesce(p_payment_result, '{}'::jsonb), '{validation_error}', '"amount_mismatch"'::jsonb, true)
    where id = v_order.id;
    raise exception 'newebpay_amount_mismatch' using errcode = '22023';
  end if;
  select * into v_product from public.commerce_products where code = v_order.product_code;
  if not found then raise exception 'newebpay_product_not_found' using errcode = 'P0002'; end if;
  update public.commerce_orders set status = 'paid', paid_at = v_start,
    provider_transaction_reference = v_transaction, payment_result = coalesce(p_payment_result, '{}'::jsonb)
  where id = v_order.id;
  update public.newebpay_checkout_sessions set used_at = v_start
  where order_id = v_order.id and used_at is null;
  insert into public.member_entitlements (
    id, user_id, kind, daily_copy_limit, starts_at, expires_at,
    external_reference, note, product_code, order_id, library_capacity
  ) values (
    v_entitlement_id, v_order.user_id, v_product.kind, v_product.daily_copy_limit,
    v_start, v_end, 'newebpay-entitlement:' || v_order.id::text,
    '藍新金流單次創作包付款', v_product.code, v_order.id, v_product.library_capacity
  );
  insert into public.image_credit_grants (
    user_id, entitlement_id, total_credits, remaining_credits, starts_at, expires_at,
    external_reference, source_kind, product_code, order_id
  ) values (
    v_order.user_id, v_entitlement_id, v_product.image_credits, v_product.image_credits,
    v_start, v_end, 'newebpay-grant:' || v_order.id::text, v_product.kind,
    v_product.code, v_order.id
  );
  return query select v_order.id, 'paid'::text, true;
end;
$$;

-- Keep sandbox behaviour identical to production so it remains useful for
-- regression checks even though it is not a recurring charge.
create or replace function public.finalize_payuni_payment(
  p_provider_order_reference text,
  p_provider_transaction_reference text,
  p_paid_amount integer,
  p_payment_status text,
  p_payment_result jsonb default '{}'::jsonb
)
returns table (order_id uuid, order_status text, newly_paid boolean)
language plpgsql security definer set search_path = public
as $$
declare
  v_order public.commerce_orders%rowtype;
  v_product public.commerce_products%rowtype;
  v_entitlement_id uuid := gen_random_uuid();
  v_start timestamptz := now();
  v_end timestamptz := now() + interval '30 days';
  v_reference text := trim(coalesce(p_provider_order_reference, ''));
  v_transaction text := nullif(trim(coalesce(p_provider_transaction_reference, '')), '');
  v_status text := upper(trim(coalesce(p_payment_status, '')));
begin
  if v_reference = '' or p_paid_amount is null or p_paid_amount < 1 then raise exception 'payuni_payment_invalid' using errcode = '22023'; end if;
  select * into v_order from public.commerce_orders o
  where o.payment_provider in ('payuni_sandbox', 'payuni') and o.provider_order_reference = v_reference for update;
  if not found then raise exception 'payuni_order_not_found' using errcode = 'P0002'; end if;
  if v_order.status = 'paid' then return query select v_order.id, v_order.status, false; return; end if;
  if v_order.status <> 'pending' then return query select v_order.id, v_order.status, false; return; end if;
  if v_status <> 'SUCCESS' then
    update public.commerce_orders set status = 'failed', provider_transaction_reference = v_transaction,
      payment_result = coalesce(p_payment_result, '{}'::jsonb) where id = v_order.id;
    return query select v_order.id, 'failed'::text, false; return;
  end if;
  if p_paid_amount <> v_order.amount_twd then raise exception 'payuni_amount_mismatch' using errcode = '22023'; end if;
  select * into v_product from public.commerce_products where code = v_order.product_code;
  if not found then raise exception 'payuni_product_not_found' using errcode = 'P0002'; end if;
  update public.commerce_orders set status = 'paid', paid_at = v_start,
    provider_transaction_reference = v_transaction, payment_result = coalesce(p_payment_result, '{}'::jsonb)
  where id = v_order.id;
  update public.payuni_checkout_sessions set used_at = v_start where order_id = v_order.id and used_at is null;
  insert into public.member_entitlements (
    id, user_id, kind, daily_copy_limit, starts_at, expires_at,
    external_reference, note, product_code, order_id, library_capacity
  ) values (
    v_entitlement_id, v_order.user_id, v_product.kind, v_product.daily_copy_limit,
    v_start, v_end, 'payuni-entitlement:' || v_order.id::text,
    'PAYUNi 沙盒單次創作包付款', v_product.code, v_order.id, v_product.library_capacity
  );
  insert into public.image_credit_grants (
    user_id, entitlement_id, total_credits, remaining_credits, starts_at, expires_at,
    external_reference, source_kind, product_code, order_id
  ) values (
    v_order.user_id, v_entitlement_id, v_product.image_credits, v_product.image_credits,
    v_start, v_end, 'payuni-grant:' || v_order.id::text, v_product.kind,
    v_product.code, v_order.id
  );
  return query select v_order.id, 'paid'::text, true;
end;
$$;

-- Sandbox checkout follows the same repeat-purchase rule as production.
-- It stays useful for regression testing without imposing legacy upgrade locks.
create or replace function public.create_my_payuni_checkout(
  p_product_code text,
  p_idempotency_key uuid
)
returns table (
  checkout_id uuid,
  order_id uuid,
  provider_order_reference text,
  product_name text,
  amount_twd integer,
  expires_at timestamptz
)
language plpgsql security definer set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_product public.commerce_products%rowtype;
  v_order public.commerce_orders%rowtype;
  v_session public.payuni_checkout_sessions%rowtype;
  v_order_id uuid := gen_random_uuid();
  v_checkout_id uuid := gen_random_uuid();
  v_reference text;
  v_expiry timestamptz := now() + interval '30 minutes';
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  if p_idempotency_key is null then raise exception 'invalid_checkout_idempotency_key' using errcode = '22023'; end if;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 64));
  select * into v_product from public.commerce_products
  where code = trim(coalesce(p_product_code, '')) and is_active;
  if not found then raise exception 'active_product_not_found' using errcode = 'P0002'; end if;

  select s.* into v_session from public.payuni_checkout_sessions s
  join public.commerce_orders o on o.id = s.order_id
  where s.user_id = v_user and s.idempotency_key = p_idempotency_key
    and s.expires_at > now() and o.status = 'pending'
  limit 1;
  if found then
    select * into v_order from public.commerce_orders where id = v_session.order_id;
    return query select v_session.id, v_order.id, v_order.provider_order_reference,
      coalesce(v_order.product_snapshot ->> 'display_name', v_order.product_code),
      v_order.amount_twd, v_session.expires_at;
    return;
  end if;

  v_reference := 'P' || to_char(clock_timestamp(), 'YYMMDDHH24MISSMS')
    || substr(replace(v_order_id::text, '-', ''), 1, 3);
  insert into public.commerce_orders (
    id, user_id, product_code, status, payment_provider, provider_order_reference,
    amount_twd, product_snapshot
  ) values (
    v_order_id, v_user, v_product.code, 'pending', 'payuni_sandbox', v_reference,
    v_product.price_twd,
    jsonb_build_object(
      'display_name', v_product.display_name, 'listed_price_twd', v_product.price_twd,
      'image_credits', v_product.image_credits, 'daily_copy_limit', v_product.daily_copy_limit,
      'library_capacity', v_product.library_capacity, 'credit_valid_days', 30,
      'billing_model', 'one_time_30_day_pack', 'payuni_sandbox', true
    )
  );
  insert into public.payuni_checkout_sessions (
    id, order_id, user_id, idempotency_key, provider, expires_at
  ) values (
    v_checkout_id, v_order_id, v_user, p_idempotency_key, 'payuni_sandbox', v_expiry
  );
  return query select v_checkout_id, v_order_id, v_reference,
    v_product.display_name, v_product.price_twd, v_expiry;
end;
$$;

-- Only active creator-tier packs unlock marketplace and expanded reel limits.
-- A 7-credit refill remains usable for generation but does not extend these rights.
create or replace function public.get_my_creator_reel_library_status()
returns table (
  eligible boolean,
  marketplace_eligible boolean,
  product_code text,
  auto_reel_limit integer,
  retention_days integer,
  collected_reel_limit integer,
  publish_template_limit integer,
  active_auto_reel_count integer,
  collected_reel_count integer,
  unlocked_template_count integer,
  shared_reel_count integer
)
language plpgsql security definer set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_entitlement record;
  v_auto_limit integer := 5;
  v_collected_limit integer := 5;
  v_publish_limit integer := 0;
  v_marketplace boolean := false;
  v_active_count integer := 0;
  v_collected_count integer := 0;
  v_unlocked_count integer := 0;
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  select * into v_entitlement from public.get_my_effective_entitlement();
  if found and v_entitlement.entitlement_kind = 'subscription' then
    v_marketplace := true;
    case v_entitlement.product_code
      when 'basic_monthly' then v_auto_limit := 20; v_collected_limit := 20; v_publish_limit := 10;
      when 'standard_monthly' then v_auto_limit := 30; v_collected_limit := 30; v_publish_limit := 15;
      when 'advanced_monthly' then v_auto_limit := 40; v_collected_limit := 40; v_publish_limit := 20;
    end case;
  end if;
  select count(*)::integer into v_active_count from public.creator_reels r
  where r.owner_id = v_user and r.status = 'active'
    and r.auto_expires_at is not null and r.auto_expires_at > now();
  select count(*)::integer into v_collected_count from public.creator_reels r
  where r.owner_id = v_user and r.status = 'collected';
  select count(*)::integer into v_unlocked_count from public.shared_template_redemptions r
  join public.shared_templates t on t.id = r.template_id
  where r.consumer_id = v_user and t.owner_id <> v_user;
  return query select true, v_marketplace, coalesce(v_entitlement.product_code, 'free'),
    v_auto_limit, 10, v_collected_limit, v_publish_limit, v_active_count,
    v_collected_count, v_unlocked_count, v_collected_count + v_unlocked_count;
end;
$$;

-- Admin QA grants use the same independent-lot model and must never expire a
-- member's earlier test purchase.
create or replace function public.admin_grant_test_purchase(
  p_user_id uuid, p_product_code text, p_note text default '管理員測試購買'
)
returns table (
  order_id uuid, product_code text, product_kind text, granted_credits integer,
  entitlement_expires_at timestamptz, daily_copy_limit integer, library_capacity integer,
  credit_expires_at timestamptz, subscription_period_end timestamptz
)
language plpgsql security definer set search_path = public
as $$
declare
  v_product public.commerce_products%rowtype;
  v_note text := trim(coalesce(p_note, '管理員測試購買'));
  v_order_id uuid := gen_random_uuid();
  v_entitlement_id uuid := gen_random_uuid();
  v_start timestamptz := now();
  v_end timestamptz := now() + interval '30 days';
begin
  if not public.is_beta_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  if p_user_id is null then raise exception 'member_required' using errcode = '22023'; end if;
  if char_length(v_note) not between 3 and 160 then raise exception 'invalid_purchase_note' using errcode = '22023'; end if;
  select * into v_product from public.commerce_products
  where code = trim(coalesce(p_product_code, '')) and is_active;
  if not found then raise exception 'active_product_not_found' using errcode = 'P0002'; end if;
  if not exists (select 1 from public.profiles where id = p_user_id) then raise exception 'member_not_found' using errcode = 'P0002'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_user_id::text, 0));
  insert into public.commerce_orders (
    id, user_id, product_code, status, payment_provider, provider_order_reference,
    amount_twd, product_snapshot, paid_at
  ) values (
    v_order_id, p_user_id, v_product.code, 'paid', 'admin_test',
    'admin-test-order:' || v_order_id::text, 0,
    jsonb_build_object(
      'display_name', v_product.display_name, 'listed_price_twd', v_product.price_twd,
      'image_credits', v_product.image_credits, 'daily_copy_limit', v_product.daily_copy_limit,
      'library_capacity', v_product.library_capacity, 'credit_valid_days', 30,
      'billing_model', 'one_time_30_day_pack', 'test_purchase', true
    ), v_start
  );
  insert into public.member_entitlements (
    id, user_id, kind, daily_copy_limit, starts_at, expires_at, external_reference,
    note, created_by, product_code, order_id, library_capacity
  ) values (
    v_entitlement_id, p_user_id, v_product.kind, v_product.daily_copy_limit,
    v_start, v_end, 'admin-test-entitlement:' || v_order_id::text,
    v_note, auth.uid(), v_product.code, v_order_id, v_product.library_capacity
  );
  insert into public.image_credit_grants (
    user_id, entitlement_id, total_credits, remaining_credits, starts_at, expires_at,
    external_reference, source_kind, product_code, order_id
  ) values (
    p_user_id, v_entitlement_id, v_product.image_credits, v_product.image_credits,
    v_start, v_end, 'admin-test-grant:' || v_order_id::text, v_product.kind,
    v_product.code, v_order_id
  );
  return query select v_order_id, v_product.code, v_product.kind, v_product.image_credits,
    v_end, v_product.daily_copy_limit, v_product.library_capacity, v_end,
    null::timestamptz;
end;
$$;

revoke all on function public.get_my_active_paid_credit_lots() from public, anon;
grant execute on function public.get_my_active_paid_credit_lots() to authenticated;
revoke all on function public.create_my_payuni_checkout(text, uuid) from public, anon;
grant execute on function public.create_my_payuni_checkout(text, uuid) to authenticated;
grant execute on function public.get_my_creator_reel_library_status() to authenticated;
revoke all on function public.admin_grant_test_purchase(uuid, text, text) from public, anon;
grant execute on function public.admin_grant_test_purchase(uuid, text, text) to authenticated;
revoke all on function public.create_my_newebpay_checkout(text, uuid) from public, anon;
grant execute on function public.create_my_newebpay_checkout(text, uuid) to authenticated;
revoke all on function public.finalize_newebpay_initial_payment(text, text, text, integer, text, jsonb) from public, anon, authenticated;
grant execute on function public.finalize_newebpay_initial_payment(text, text, text, integer, text, jsonb) to service_role;
revoke all on function public.finalize_payuni_payment(text, text, integer, text, jsonb) from public, anon, authenticated;
grant execute on function public.finalize_payuni_payment(text, text, integer, text, jsonb) to service_role;

notify pgrst, 'reload schema';
