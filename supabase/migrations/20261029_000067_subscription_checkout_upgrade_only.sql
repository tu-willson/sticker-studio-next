-- While a paid subscription is active, only a strictly higher subscription
-- plan can start a new checkout.  Generation packs remain independently
-- purchasable.  The UI mirrors this rule, but the database is authoritative.
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
  v_active_subscription_code text;
  v_requested_rank integer := 0;
  v_active_rank integer := 0;
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
        when 'basic_monthly' then 1
        when 'standard_monthly' then 2
        when 'advanced_monthly' then 3
        else 0
      end;
      v_active_rank := case v_active_subscription_code
        when 'basic_monthly' then 1
        when 'standard_monthly' then 2
        when 'advanced_monthly' then 3
        else 99
      end;
      if v_requested_rank <= v_active_rank then
        raise exception 'subscription_plan_not_upgrade' using errcode = '22023';
      end if;
    end if;
  end if;

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

  -- PAYUNi MerTradeNo is limited to 20 ASCII characters. The random suffix
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

revoke all on function public.create_my_payuni_checkout(text, uuid) from public, anon;
grant execute on function public.create_my_payuni_checkout(text, uuid) to authenticated;

notify pgrst, 'reload schema';
