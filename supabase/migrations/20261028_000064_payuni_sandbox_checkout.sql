-- PAYUNi sandbox checkout foundation.
--
-- A browser may create a pending order for itself, but only the server-side
-- PAYUNi NotifyURL handler may complete it and issue paid entitlements.

alter table public.commerce_orders
  add column if not exists provider_transaction_reference text,
  add column if not exists payment_result jsonb not null default '{}'::jsonb
    check (jsonb_typeof(payment_result) = 'object');

create unique index if not exists commerce_orders_provider_transaction_reference_unique
  on public.commerce_orders (payment_provider, provider_transaction_reference)
  where provider_transaction_reference is not null;

create table if not exists public.payuni_checkout_sessions (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null unique references public.commerce_orders(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  idempotency_key uuid not null,
  provider text not null check (provider in ('payuni_sandbox', 'payuni')),
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  used_at timestamptz,
  unique (user_id, idempotency_key),
  check (expires_at > created_at)
);

create index if not exists payuni_checkout_sessions_expires_idx
  on public.payuni_checkout_sessions (expires_at);

alter table public.payuni_checkout_sessions enable row level security;
revoke all on table public.payuni_checkout_sessions from anon, authenticated;
grant select, insert, update, delete on table public.payuni_checkout_sessions to service_role;

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
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_idempotency_key is null then
    raise exception 'invalid_checkout_idempotency_key' using errcode = '22023';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 64));

  select s.* into v_session
  from public.payuni_checkout_sessions s
  join public.commerce_orders o on o.id = s.order_id
  where s.user_id = v_user
    and s.idempotency_key = p_idempotency_key
    and s.expires_at > now()
    and o.status = 'pending'
  limit 1;
  if found then
    select * into v_order from public.commerce_orders where id = v_session.order_id;
    return query select v_session.id, v_order.id, v_order.provider_order_reference,
      coalesce(v_order.product_snapshot ->> 'display_name', v_order.product_code),
      v_order.amount_twd, v_session.expires_at;
    return;
  end if;

  select * into v_product
  from public.commerce_products
  where code = trim(coalesce(p_product_code, '')) and is_active;
  if not found then
    raise exception 'active_product_not_found' using errcode = 'P0002';
  end if;

  -- PAYUNi MerTradeNo is limited to 20 ASCII characters.  The random suffix
  -- prevents a collision if two members begin checkout in the same millisecond.
  v_reference := 'P' || to_char(clock_timestamp(), 'YYMMDDHH24MISSMS')
    || substr(replace(v_order_id::text, '-', ''), 1, 3);

  insert into public.commerce_orders (
    id, user_id, product_code, status, payment_provider, provider_order_reference,
    amount_twd, product_snapshot
  ) values (
    v_order_id, v_user, v_product.code, 'pending', 'payuni_sandbox', v_reference,
    v_product.price_twd,
    jsonb_build_object(
      'display_name', v_product.display_name,
      'listed_price_twd', v_product.price_twd,
      'image_credits', v_product.image_credits,
      'daily_copy_limit', v_product.daily_copy_limit,
      'library_capacity', v_product.library_capacity,
      'credit_valid_days', v_product.credit_valid_days,
      'billing_interval_months', v_product.billing_interval_months,
      'payuni_sandbox', true
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

create or replace function public.get_my_commerce_order_status(p_order_id uuid)
returns table (
  order_id uuid,
  status text,
  product_code text,
  product_name text,
  amount_twd integer,
  paid_at timestamptz,
  created_at timestamptz
)
language sql security definer set search_path = public
as $$
  select o.id, o.status, o.product_code,
    coalesce(o.product_snapshot ->> 'display_name', o.product_code),
    o.amount_twd, o.paid_at, o.created_at
  from public.commerce_orders o
  where o.id = p_order_id and o.user_id = auth.uid();
$$;

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
  v_subscription_id uuid;
  v_start timestamptz := now();
  v_entitlement_end timestamptz;
  v_credit_end timestamptz;
  v_reference text := trim(coalesce(p_provider_order_reference, ''));
  v_transaction text := nullif(trim(coalesce(p_provider_transaction_reference, '')), '');
  v_status text := upper(trim(coalesce(p_payment_status, '')));
begin
  if v_reference = '' then
    raise exception 'payuni_order_reference_required' using errcode = '22023';
  end if;
  if p_paid_amount is null or p_paid_amount < 1 then
    raise exception 'payuni_amount_invalid' using errcode = '22023';
  end if;

  select * into v_order
  from public.commerce_orders o
  where o.payment_provider in ('payuni_sandbox', 'payuni')
    and o.provider_order_reference = v_reference
  for update;
  if not found then
    raise exception 'payuni_order_not_found' using errcode = 'P0002';
  end if;

  if v_order.status = 'paid' then
    return query select v_order.id, v_order.status, false;
    return;
  end if;

  if v_order.status <> 'pending' then
    return query select v_order.id, v_order.status, false;
    return;
  end if;

  if v_status <> 'SUCCESS' then
    update public.commerce_orders
    set status = 'failed', provider_transaction_reference = v_transaction,
      payment_result = coalesce(p_payment_result, '{}'::jsonb)
    where id = v_order.id;
    return query select v_order.id, 'failed'::text, false;
    return;
  end if;

  if p_paid_amount <> v_order.amount_twd then
    update public.commerce_orders
    set status = 'failed', provider_transaction_reference = v_transaction,
      payment_result = jsonb_set(
        coalesce(p_payment_result, '{}'::jsonb), '{validation_error}', '"amount_mismatch"'::jsonb, true
      )
    where id = v_order.id;
    raise exception 'payuni_amount_mismatch' using errcode = '22023';
  end if;

  select * into v_product from public.commerce_products where code = v_order.product_code;
  if not found then
    raise exception 'payuni_product_not_found' using errcode = 'P0002';
  end if;

  update public.commerce_orders
  set status = 'paid', paid_at = v_start, provider_transaction_reference = v_transaction,
    payment_result = coalesce(p_payment_result, '{}'::jsonb)
  where id = v_order.id;
  update public.payuni_checkout_sessions
  set used_at = v_start
  where order_id = v_order.id and used_at is null;

  perform pg_advisory_xact_lock(hashtextextended(v_order.user_id::text, 0));
  if v_product.kind = 'subscription' then
    -- Sandbox UPP is a one-time card payment.  It grants one billing period;
    -- real automatic renewal will be added only after PAYUNi recurring payment
    -- approval is available.
    update public.member_subscriptions
    set status = 'expired', current_period_end = least(current_period_end, v_start)
    where user_id = v_order.user_id and status in ('active', 'past_due');
    update public.member_entitlements
    set expires_at = least(expires_at, v_start)
    where user_id = v_order.user_id and kind = 'subscription' and expires_at > v_start;
    v_entitlement_end := v_start + make_interval(months => v_product.billing_interval_months);
    v_credit_end := v_entitlement_end + make_interval(months => v_product.billing_interval_months);
    v_subscription_id := gen_random_uuid();
    insert into public.member_subscriptions (
      id, user_id, product_code, status, provider_subscription_reference,
      current_period_start, current_period_end, latest_order_id
    ) values (
      v_subscription_id, v_order.user_id, v_product.code, 'active',
      'payuni-sandbox-one-time:' || v_order.id::text,
      v_start, v_entitlement_end, v_order.id
    );
  else
    v_entitlement_end := v_start + make_interval(days => v_product.credit_valid_days);
    v_credit_end := v_entitlement_end;
  end if;

  insert into public.member_entitlements (
    id, user_id, kind, daily_copy_limit, starts_at, expires_at,
    external_reference, note, product_code, order_id, library_capacity
  ) values (
    v_entitlement_id, v_order.user_id, v_product.kind, v_product.daily_copy_limit,
    v_start, v_entitlement_end, 'payuni-entitlement:' || v_order.id::text,
    'PAYUNi 沙盒付款', v_product.code, v_order.id, v_product.library_capacity
  );
  insert into public.image_credit_grants (
    user_id, entitlement_id, total_credits, remaining_credits, starts_at, expires_at,
    external_reference, source_kind, product_code, order_id, subscription_id
  ) values (
    v_order.user_id, v_entitlement_id, v_product.image_credits, v_product.image_credits,
    v_start, v_credit_end, 'payuni-grant:' || v_order.id::text, v_product.kind,
    v_product.code, v_order.id, v_subscription_id
  );

  return query select v_order.id, 'paid'::text, true;
end;
$$;

revoke all on function public.create_my_payuni_checkout(text, uuid) from public, anon;
revoke all on function public.get_my_commerce_order_status(uuid) from public, anon;
revoke all on function public.finalize_payuni_payment(text, text, integer, text, jsonb)
  from public, anon, authenticated;
grant execute on function public.create_my_payuni_checkout(text, uuid) to authenticated;
grant execute on function public.get_my_commerce_order_status(uuid) to authenticated;
grant execute on function public.finalize_payuni_payment(text, text, integer, text, jsonb)
  to service_role;

notify pgrst, 'reload schema';
