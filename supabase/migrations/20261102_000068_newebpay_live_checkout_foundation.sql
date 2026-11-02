-- NewebPay formal checkout foundation.
--
-- This integration is deliberately dark-launched: Edge Functions additionally
-- require the NEWEBPAY_LIVE_ENABLED secret before issuing a payment form.
-- Browser users can create only their own pending order, while verified
-- NewebPay NotifyURL callbacks are the only path that issues entitlements.

create table if not exists public.newebpay_checkout_sessions (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null unique references public.commerce_orders(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  idempotency_key uuid not null,
  payment_flow text not null check (payment_flow in ('mpg', 'periodic')),
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  used_at timestamptz,
  unique (user_id, idempotency_key),
  check (expires_at > created_at)
);

create index if not exists newebpay_checkout_sessions_expires_idx
  on public.newebpay_checkout_sessions (expires_at);

create table if not exists public.newebpay_subscription_profiles (
  subscription_id uuid primary key references public.member_subscriptions(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  provider_period_no text not null unique,
  initial_order_id uuid not null unique references public.commerce_orders(id) on delete restrict,
  product_code text not null references public.commerce_products(code) on delete restrict,
  amount_twd integer not null check (amount_twd > 0),
  product_snapshot jsonb not null check (jsonb_typeof(product_snapshot) = 'object'),
  latest_authorized_times integer not null default 1 check (latest_authorized_times >= 0),
  last_paid_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger newebpay_subscription_profiles_set_updated_at
before update on public.newebpay_subscription_profiles
for each row execute function public.set_updated_at();

create table if not exists public.newebpay_periodic_events (
  id uuid primary key default gen_random_uuid(),
  provider_period_no text not null,
  provider_order_reference text not null,
  provider_transaction_reference text,
  status text not null,
  amount_twd integer,
  payment_result jsonb not null default '{}'::jsonb check (jsonb_typeof(payment_result) = 'object'),
  created_at timestamptz not null default now(),
  unique (provider_period_no, provider_order_reference),
  unique (provider_transaction_reference)
);

alter table public.newebpay_checkout_sessions enable row level security;
alter table public.newebpay_subscription_profiles enable row level security;
alter table public.newebpay_periodic_events enable row level security;
revoke all on table public.newebpay_checkout_sessions, public.newebpay_subscription_profiles,
  public.newebpay_periodic_events from anon, authenticated;
grant select, insert, update, delete on table public.newebpay_checkout_sessions,
  public.newebpay_subscription_profiles, public.newebpay_periodic_events to service_role;

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
  v_active_subscription_code text;
  v_requested_rank integer := 0;
  v_active_rank integer := 0;
  v_order_id uuid := gen_random_uuid();
  v_checkout_id uuid := gen_random_uuid();
  v_reference text;
  v_flow text;
  v_expiry timestamptz := now() + interval '15 minutes';
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_idempotency_key is null then
    raise exception 'invalid_checkout_idempotency_key' using errcode = '22023';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 64));
  select * into v_product
  from public.commerce_products
  where code = trim(coalesce(p_product_code, '')) and is_active;
  if not found then
    raise exception 'active_product_not_found' using errcode = 'P0002';
  end if;

  if v_product.kind = 'subscription' then
    select e.product_code into v_active_subscription_code
    from public.member_entitlements e
    where e.user_id = v_user and e.kind = 'subscription'
      and e.starts_at <= now() and e.expires_at > now()
    order by e.expires_at desc, e.created_at desc
    limit 1;
    if found then
      v_requested_rank := case v_product.code
        when 'basic_monthly' then 1 when 'standard_monthly' then 2
        when 'advanced_monthly' then 3 else 0 end;
      v_active_rank := case v_active_subscription_code
        when 'basic_monthly' then 1 when 'standard_monthly' then 2
        when 'advanced_monthly' then 3 else 99 end;
      if v_requested_rank <= v_active_rank then
        raise exception 'subscription_plan_not_upgrade' using errcode = '22023';
      end if;
    end if;
  end if;

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
      v_order.amount_twd, v_session.payment_flow, v_session.expires_at;
    return;
  end if;

  v_flow := case when v_product.kind = 'subscription' then 'periodic' else 'mpg' end;
  -- NewebPay accepts 30 ASCII characters; this 21-character reference is unique
  -- even if two purchase requests begin during the same millisecond.
  v_reference := 'N' || to_char(clock_timestamp(), 'YYMMDDHH24MISSMS')
    || substr(replace(v_order_id::text, '-', ''), 1, 3);

  insert into public.commerce_orders (
    id, user_id, product_code, status, payment_provider, provider_order_reference,
    amount_twd, product_snapshot
  ) values (
    v_order_id, v_user, v_product.code, 'pending',
    case when v_flow = 'periodic' then 'newebpay_periodic' else 'newebpay_mpg' end,
    v_reference, v_product.price_twd,
    jsonb_build_object(
      'display_name', v_product.display_name, 'listed_price_twd', v_product.price_twd,
      'image_credits', v_product.image_credits, 'daily_copy_limit', v_product.daily_copy_limit,
      'library_capacity', v_product.library_capacity, 'credit_valid_days', v_product.credit_valid_days,
      'billing_interval_months', v_product.billing_interval_months, 'payment_provider', 'newebpay'
    )
  );
  insert into public.newebpay_checkout_sessions (
    id, order_id, user_id, idempotency_key, payment_flow, expires_at
  ) values (v_checkout_id, v_order_id, v_user, p_idempotency_key, v_flow, v_expiry);

  return query select v_checkout_id, v_order_id, v_reference, v_product.display_name,
    v_product.price_twd, v_flow, v_expiry;
end;
$$;

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
  v_subscription_id uuid;
  v_start timestamptz := now();
  v_entitlement_end timestamptz;
  v_credit_end timestamptz;
  v_reference text := trim(coalesce(p_provider_order_reference, ''));
  v_transaction text := nullif(trim(coalesce(p_provider_transaction_reference, '')), '');
  v_period text := nullif(trim(coalesce(p_provider_period_reference, '')), '');
  v_status text := upper(trim(coalesce(p_payment_status, '')));
begin
  if v_reference = '' or p_paid_amount is null or p_paid_amount < 1 then
    raise exception 'newebpay_payment_reference_or_amount_invalid' using errcode = '22023';
  end if;
  select * into v_order from public.commerce_orders o
  where o.payment_provider in ('newebpay_mpg', 'newebpay_periodic')
    and o.provider_order_reference = v_reference
  for update;
  if not found then raise exception 'newebpay_order_not_found' using errcode = 'P0002'; end if;
  if v_order.status = 'paid' then
    return query select v_order.id, v_order.status, false; return;
  end if;
  if v_order.status <> 'pending' then
    return query select v_order.id, v_order.status, false; return;
  end if;
  if v_status <> 'SUCCESS' then
    update public.commerce_orders set status = 'failed', provider_transaction_reference = v_transaction,
      payment_result = coalesce(p_payment_result, '{}'::jsonb) where id = v_order.id;
    return query select v_order.id, 'failed'::text, false; return;
  end if;
  if p_paid_amount <> v_order.amount_twd then
    update public.commerce_orders set status = 'failed', provider_transaction_reference = v_transaction,
      payment_result = jsonb_set(coalesce(p_payment_result, '{}'::jsonb), '{validation_error}',
      '"amount_mismatch"'::jsonb, true) where id = v_order.id;
    raise exception 'newebpay_amount_mismatch' using errcode = '22023';
  end if;
  select * into v_product from public.commerce_products where code = v_order.product_code;
  if not found then raise exception 'newebpay_product_not_found' using errcode = 'P0002'; end if;
  if v_product.kind = 'subscription' and v_period is null then
    raise exception 'newebpay_period_reference_required' using errcode = '22023';
  end if;

  update public.commerce_orders set status = 'paid', paid_at = v_start,
    provider_transaction_reference = v_transaction, payment_result = coalesce(p_payment_result, '{}'::jsonb)
  where id = v_order.id;
  update public.newebpay_checkout_sessions set used_at = v_start
  where order_id = v_order.id and used_at is null;

  perform pg_advisory_xact_lock(hashtextextended(v_order.user_id::text, 0));
  if v_product.kind = 'subscription' then
    update public.member_subscriptions set status = 'expired', current_period_end = least(current_period_end, v_start)
    where user_id = v_order.user_id and status in ('active', 'past_due');
    update public.member_entitlements set expires_at = least(expires_at, v_start)
    where user_id = v_order.user_id and kind = 'subscription' and expires_at > v_start;
    v_entitlement_end := v_start + interval '30 days';
    v_credit_end := v_entitlement_end + interval '30 days';
    v_subscription_id := gen_random_uuid();
    insert into public.member_subscriptions (
      id, user_id, product_code, status, provider_subscription_reference,
      current_period_start, current_period_end, latest_order_id
    ) values (v_subscription_id, v_order.user_id, v_product.code, 'active',
      'newebpay:' || v_period, v_start, v_entitlement_end, v_order.id);
    insert into public.newebpay_subscription_profiles (
      subscription_id, user_id, provider_period_no, initial_order_id, product_code,
      amount_twd, product_snapshot, latest_authorized_times, last_paid_at
    ) values (v_subscription_id, v_order.user_id, v_period, v_order.id, v_product.code,
      v_order.amount_twd, v_order.product_snapshot,
      coalesce((p_payment_result ->> 'authorized_times')::integer, 1), v_start);
  else
    v_entitlement_end := v_start + make_interval(days => v_product.credit_valid_days);
    v_credit_end := v_entitlement_end;
  end if;
  insert into public.member_entitlements (
    id, user_id, kind, daily_copy_limit, starts_at, expires_at, external_reference,
    note, product_code, order_id, library_capacity
  ) values (v_entitlement_id, v_order.user_id, v_product.kind, v_product.daily_copy_limit,
    v_start, v_entitlement_end, 'newebpay-entitlement:' || v_order.id::text,
    '藍新金流付款', v_product.code, v_order.id, v_product.library_capacity);
  insert into public.image_credit_grants (
    user_id, entitlement_id, total_credits, remaining_credits, starts_at, expires_at,
    external_reference, source_kind, product_code, order_id, subscription_id
  ) values (v_order.user_id, v_entitlement_id, v_product.image_credits, v_product.image_credits,
    v_start, v_credit_end, 'newebpay-grant:' || v_order.id::text, v_product.kind,
    v_product.code, v_order.id, v_subscription_id);
  return query select v_order.id, 'paid'::text, true;
end;
$$;

create or replace function public.record_newebpay_periodic_renewal(
  p_provider_period_reference text,
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
  v_profile public.newebpay_subscription_profiles%rowtype;
  v_subscription public.member_subscriptions%rowtype;
  v_product public.commerce_products%rowtype;
  v_order_id uuid := gen_random_uuid();
  v_entitlement_id uuid := gen_random_uuid();
  v_start timestamptz := now();
  v_end timestamptz;
  v_credit_end timestamptz;
  v_period text := trim(coalesce(p_provider_period_reference, ''));
  v_order_ref text := trim(coalesce(p_provider_order_reference, ''));
  v_transaction text := nullif(trim(coalesce(p_provider_transaction_reference, '')), '');
  v_status text := upper(trim(coalesce(p_payment_status, '')));
begin
  if v_period = '' or v_order_ref = '' or p_paid_amount is null or p_paid_amount < 1 then
    raise exception 'newebpay_renewal_reference_or_amount_invalid' using errcode = '22023';
  end if;
  select * into v_profile from public.newebpay_subscription_profiles
  where provider_period_no = v_period for update;
  if not found then raise exception 'newebpay_period_not_found' using errcode = 'P0002'; end if;
  if exists (select 1 from public.newebpay_periodic_events
    where provider_period_no = v_period and provider_order_reference = v_order_ref) then
    return query select null::uuid, 'duplicate'::text, false; return;
  end if;
  select * into v_subscription from public.member_subscriptions where id = v_profile.subscription_id for update;
  if not found then raise exception 'newebpay_subscription_not_found' using errcode = 'P0002'; end if;
  if v_status <> 'SUCCESS' or p_paid_amount <> v_profile.amount_twd then
    insert into public.newebpay_periodic_events (
      provider_period_no, provider_order_reference, provider_transaction_reference, status, amount_twd, payment_result
    ) values (v_period, v_order_ref, v_transaction,
      case when p_paid_amount <> v_profile.amount_twd then 'amount_mismatch' else lower(v_status) end,
      p_paid_amount, coalesce(p_payment_result, '{}'::jsonb));
    update public.member_subscriptions set status = 'past_due'
    where id = v_subscription.id and status = 'active';
    return query select null::uuid, 'failed'::text, false; return;
  end if;
  select * into v_product from public.commerce_products where code = v_profile.product_code;
  if not found or v_product.kind <> 'subscription' then
    raise exception 'newebpay_subscription_product_not_found' using errcode = 'P0002';
  end if;
  v_end := v_start + interval '30 days';
  v_credit_end := v_end + interval '30 days';
  insert into public.commerce_orders (
    id, user_id, product_code, status, payment_provider, provider_order_reference,
    provider_transaction_reference, amount_twd, product_snapshot, paid_at, payment_result
  ) values (v_order_id, v_profile.user_id, v_profile.product_code, 'paid', 'newebpay_periodic_renewal',
    v_order_ref, v_transaction, v_profile.amount_twd, v_profile.product_snapshot, v_start,
    coalesce(p_payment_result, '{}'::jsonb));
  insert into public.newebpay_periodic_events (
    provider_period_no, provider_order_reference, provider_transaction_reference, status, amount_twd, payment_result
  ) values (v_period, v_order_ref, v_transaction, 'success', p_paid_amount,
    coalesce(p_payment_result, '{}'::jsonb));
  insert into public.member_entitlements (
    id, user_id, kind, daily_copy_limit, starts_at, expires_at, external_reference,
    note, product_code, order_id, library_capacity
  ) values (v_entitlement_id, v_profile.user_id, 'subscription', v_product.daily_copy_limit,
    v_start, v_end, 'newebpay-renewal:' || v_order_id::text, '藍新金流定期定額續訂',
    v_profile.product_code, v_order_id, v_product.library_capacity);
  insert into public.image_credit_grants (
    user_id, entitlement_id, total_credits, remaining_credits, starts_at, expires_at,
    external_reference, source_kind, product_code, order_id, subscription_id
  ) values (v_profile.user_id, v_entitlement_id, v_product.image_credits, v_product.image_credits,
    v_start, v_credit_end, 'newebpay-renewal-grant:' || v_order_id::text, 'subscription',
    v_profile.product_code, v_order_id, v_subscription.id);
  update public.member_subscriptions set status = 'active', current_period_start = v_start,
    current_period_end = v_end, latest_order_id = v_order_id where id = v_subscription.id;
  update public.newebpay_subscription_profiles set latest_authorized_times = greatest(
    latest_authorized_times, coalesce((p_payment_result ->> 'authorized_times')::integer, latest_authorized_times + 1)),
    last_paid_at = v_start where subscription_id = v_subscription.id;
  return query select v_order_id, 'paid'::text, true;
end;
$$;

revoke all on function public.create_my_newebpay_checkout(text, uuid) from public, anon;
revoke all on function public.finalize_newebpay_initial_payment(text, text, text, integer, text, jsonb)
  from public, anon, authenticated;
revoke all on function public.record_newebpay_periodic_renewal(text, text, text, integer, text, jsonb)
  from public, anon, authenticated;
grant execute on function public.create_my_newebpay_checkout(text, uuid) to authenticated;
grant execute on function public.finalize_newebpay_initial_payment(text, text, text, integer, text, jsonb)
  to service_role;
grant execute on function public.record_newebpay_periodic_renewal(text, text, text, integer, text, jsonb)
  to service_role;

notify pgrst, 'reload schema';
