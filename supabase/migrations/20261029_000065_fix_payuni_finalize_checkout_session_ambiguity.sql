-- The RETURNS TABLE output parameter `order_id` is also a PL/pgSQL variable.
-- Qualify the checkout-session column so a successful PAYUNi callback does not
-- abort with PostgreSQL 42702 (ambiguous_column).
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
  update public.payuni_checkout_sessions as pcs
  set used_at = v_start
  where pcs.order_id = v_order.id and pcs.used_at is null;

  perform pg_advisory_xact_lock(hashtextextended(v_order.user_id::text, 0));
  if v_product.kind = 'subscription' then
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

revoke all on function public.finalize_payuni_payment(text, text, integer, text, jsonb)
  from public, anon, authenticated;
grant execute on function public.finalize_payuni_payment(text, text, integer, text, jsonb)
  to service_role;

notify pgrst, 'reload schema';
