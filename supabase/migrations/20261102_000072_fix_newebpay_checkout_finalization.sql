-- Qualify the checkout-session columns.  The return-table column named
-- order_id is also a PL/pgSQL variable, so an unqualified WHERE order_id is
-- ambiguous at runtime and prevents a verified NewebPay payment from being
-- finalised.
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
  update public.newebpay_checkout_sessions as checkout_session set used_at = v_start
  where checkout_session.order_id = v_order.id and checkout_session.used_at is null;
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

notify pgrst, 'reload schema';
