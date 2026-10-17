-- Sticker Studio Next: complete the entitlement resolver and add an
-- administrator-only test purchase path.  Test purchases never call a
-- payment provider and are labelled/audited as admin_test orders.

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
         coalesce(e.library_capacity, 5), e.expires_at
  from public.member_entitlements e
  where e.user_id = v_user and e.kind = 'subscription'
    and e.starts_at <= now() and e.expires_at > now()
  order by e.expires_at desc, e.created_at desc limit 1;
  if found then return; end if;

  return query
  select e.id, e.product_code, e.kind, e.daily_copy_limit,
         coalesce(e.library_capacity, 5), e.expires_at
  from public.member_entitlements e
  where e.user_id = v_user and e.kind = 'credit_pack'
    and e.starts_at <= now() and e.expires_at > now()
  order by e.daily_copy_limit desc, coalesce(e.library_capacity, 5) desc,
           e.expires_at desc, e.created_at desc limit 1;
  if found then return; end if;

  return query select null::uuid, null::text, 'free'::text, 2, 5, null::timestamptz;
end;
$$;

create or replace function public.get_my_membership_status()
returns table (
  product_code text,
  entitlement_kind text,
  daily_copy_limit integer,
  library_capacity integer,
  entitlement_expires_at timestamptz,
  subscription_period_end timestamptz
)
language plpgsql security definer set search_path = public
as $$
declare v_user uuid := auth.uid();
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  return query
  select e.product_code, e.entitlement_kind, e.daily_copy_limit,
         e.library_capacity, e.expires_at,
         (select s.current_period_end from public.member_subscriptions s
          where s.user_id = v_user and s.status in ('active', 'past_due')
          order by s.current_period_end desc limit 1)
  from public.get_my_effective_entitlement() e;
end;
$$;

create or replace function public.get_my_copy_usage_status()
returns table (
  usage_date date, daily_limit integer, used_count integer,
  remaining_count integer, entitlement_kind text, entitlement_expires_at timestamptz
)
language plpgsql security definer set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_today date := (now() at time zone 'Asia/Taipei')::date;
  v_entitlement record;
  v_used integer := 0;
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  select * into v_entitlement from public.get_my_effective_entitlement();
  select count(*)::integer into v_used from public.copy_generation_usage u
  where u.user_id = v_user and u.usage_date = v_today
    and u.status in ('reserved', 'succeeded');
  return query select v_today, v_entitlement.daily_copy_limit, v_used,
    greatest(v_entitlement.daily_copy_limit - v_used, 0),
    v_entitlement.entitlement_kind, v_entitlement.expires_at;
end;
$$;

create or replace function public.reserve_copy_generation(p_idempotency_key uuid)
returns table (
  job_id uuid, granted boolean, usage_date date, daily_limit integer,
  remaining_count integer, entitlement_kind text, entitlement_expires_at timestamptz
)
language plpgsql security definer set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_today date := (now() at time zone 'Asia/Taipei')::date;
  v_entitlement record;
  v_existing public.copy_generation_usage%rowtype;
  v_used integer := 0;
  v_job_id uuid;
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  if p_idempotency_key is null then raise exception 'invalid_idempotency_key' using errcode = '22023'; end if;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 2));
  update public.copy_generation_usage u set status = 'failed', completed_at = now(), failure_code = 'stale_timeout'
  where u.user_id = v_user and u.status = 'reserved' and u.created_at < now() - interval '10 minutes';
  select * into v_entitlement from public.get_my_effective_entitlement();
  select * into v_existing from public.copy_generation_usage u
  where u.user_id = v_user and u.idempotency_key = p_idempotency_key;
  if found then
    select count(*)::integer into v_used from public.copy_generation_usage u
    where u.user_id = v_user and u.usage_date = v_today and u.status in ('reserved', 'succeeded');
    return query select v_existing.id, v_existing.status in ('reserved', 'succeeded'), v_today,
      v_entitlement.daily_copy_limit, greatest(v_entitlement.daily_copy_limit - v_used, 0),
      v_entitlement.entitlement_kind, v_entitlement.expires_at;
    return;
  end if;
  select count(*)::integer into v_used from public.copy_generation_usage u
  where u.user_id = v_user and u.usage_date = v_today and u.status in ('reserved', 'succeeded');
  if v_used >= v_entitlement.daily_copy_limit then
    return query select null::uuid, false, v_today, v_entitlement.daily_copy_limit, 0,
      v_entitlement.entitlement_kind, v_entitlement.expires_at;
    return;
  end if;
  v_job_id := gen_random_uuid();
  insert into public.copy_generation_usage (
    id, user_id, idempotency_key, usage_date, entitlement_id, daily_limit_snapshot, status
  ) values (
    v_job_id, v_user, p_idempotency_key, v_today, v_entitlement.entitlement_id,
    v_entitlement.daily_copy_limit, 'reserved'
  );
  return query select v_job_id, true, v_today, v_entitlement.daily_copy_limit,
    v_entitlement.daily_copy_limit - v_used - 1,
    v_entitlement.entitlement_kind, v_entitlement.expires_at;
end;
$$;

create or replace function public.get_my_library_status()
returns table (saved_count integer, library_capacity integer)
language plpgsql security definer set search_path = public
as $$
declare v_user uuid := auth.uid(); v_entitlement record;
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  select * into v_entitlement from public.get_my_effective_entitlement();
  return query select count(*)::integer, v_entitlement.library_capacity
  from public.library_projects lp
  where lp.owner_id = v_user and lp.status in ('uploading', 'active');
end;
$$;

drop function if exists public.create_library_project_slot(uuid, text, jsonb);
create function public.create_library_project_slot(
  p_project_id uuid, p_title text, p_metadata jsonb default '{}'::jsonb
)
returns table (project_id uuid, saved_count integer, library_capacity integer)
language plpgsql security definer set search_path = public
as $$
declare
  v_user uuid := auth.uid(); v_entitlement record; v_capacity integer; v_count integer;
  v_title text := trim(coalesce(p_title, '')); v_prefix text;
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  if p_project_id is null or char_length(v_title) not between 1 and 80 then
    raise exception 'invalid_library_project' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 1));
  delete from public.library_projects where owner_id = v_user and status = 'uploading'
    and created_at < now() - interval '30 minutes';
  select * into v_entitlement from public.get_my_effective_entitlement();
  v_capacity := v_entitlement.library_capacity;
  select count(*)::integer into v_count from public.library_projects
  where owner_id = v_user and status in ('uploading', 'active');
  if v_count >= v_capacity then
    return query select null::uuid, v_count, v_capacity; return;
  end if;
  v_prefix := v_user::text || '/' || p_project_id::text || '/';
  insert into public.library_projects (
    id, owner_id, title, metadata, preview_path, sticker_prefix, source_photo_path, style_reference_path
  ) values (
    p_project_id, v_user, v_title, coalesce(p_metadata, '{}'::jsonb),
    v_prefix || 'preview.webp', v_prefix || 'stickers/',
    v_prefix || 'source-photo.webp', v_prefix || 'style-reference.webp'
  );
  return query select p_project_id, v_count + 1, v_capacity;
end;
$$;

create or replace function public.get_my_credit_balance_for_user(p_user_id uuid)
returns integer language sql security definer set search_path = public
as $$
  select coalesce(sum(g.remaining_credits), 0)::integer
  from public.image_credit_grants g
  where g.user_id = p_user_id and g.starts_at <= now() and g.expires_at > now()
    and g.remaining_credits > 0;
$$;

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
  if not exists (select 1 from public.profiles where id = p_user_id) then
    raise exception 'member_not_found' using errcode = 'P0002'; end if;
  if p_delta < 0 and public.get_my_credit_balance_for_user(p_user_id) < -p_delta then
    raise exception 'negative_credit_balance_not_allowed' using errcode = '22023'; end if;
  insert into public.credit_admin_adjustments (id, actor_user_id, target_user_id, delta, note)
  values (v_adjustment_id, v_actor, p_user_id, p_delta, v_note);
  if p_delta > 0 then
    insert into public.image_credit_grants (
      user_id, total_credits, remaining_credits, starts_at, expires_at, external_reference, source_kind
    ) values (
      p_user_id, p_delta, p_delta, now(), now() + interval '365 days',
      'admin-adjustment:' || v_adjustment_id::text, 'admin'
    );
  else
    v_remaining := -p_delta;
    for v_grant in
      select id, remaining_credits from public.image_credit_grants
      where user_id = p_user_id and starts_at <= now() and expires_at > now()
        and remaining_credits > 0
      order by expires_at asc, created_at asc for update
    loop
      exit when v_remaining = 0;
      update public.image_credit_grants
      set remaining_credits = remaining_credits - least(remaining_credits, v_remaining)
      where id = v_grant.id;
      v_remaining := v_remaining - least(v_grant.remaining_credits, v_remaining);
    end loop;
  end if;
  return query select p_user_id, public.get_my_credit_balance_for_user(p_user_id);
end;
$$;

create or replace function public.admin_list_members(p_search text default '')
returns table (user_id uuid, email text, display_name text, credit_balance integer, created_at timestamptz)
language plpgsql security definer set search_path = public
as $$
declare v_search text := lower(trim(coalesce(p_search, '')));
begin
  if not public.is_beta_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  return query
  select p.id, p.email, p.display_name,
    coalesce(sum(g.remaining_credits) filter (where g.starts_at <= now() and g.expires_at > now()), 0)::integer,
    p.created_at
  from public.profiles p left join public.image_credit_grants g on g.user_id = p.id
  where v_search = '' or lower(p.email) like '%' || v_search || '%'
    or lower(p.display_name) like '%' || v_search || '%'
  group by p.id, p.email, p.display_name, p.created_at
  order by p.created_at desc limit 100;
end;
$$;

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
  v_subscription_id uuid;
  v_start timestamptz := now();
  v_entitlement_end timestamptz;
  v_credit_end timestamptz;
begin
  if not public.is_beta_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  if p_user_id is null then raise exception 'member_required' using errcode = '22023'; end if;
  if char_length(v_note) not between 3 and 160 then raise exception 'invalid_purchase_note' using errcode = '22023'; end if;
  select * into v_product from public.commerce_products
  where code = trim(coalesce(p_product_code, '')) and is_active;
  if not found then raise exception 'active_product_not_found' using errcode = 'P0002'; end if;
  if not exists (select 1 from public.profiles where id = p_user_id) then
    raise exception 'member_not_found' using errcode = 'P0002'; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_user_id::text, 0));

  insert into public.commerce_orders (
    id, user_id, product_code, status, payment_provider, provider_order_reference,
    amount_twd, product_snapshot, paid_at
  ) values (
    v_order_id, p_user_id, v_product.code, 'paid', 'admin_test',
    'admin-test-order:' || v_order_id::text, 0,
    jsonb_build_object(
      'display_name', v_product.display_name,
      'listed_price_twd', v_product.price_twd,
      'image_credits', v_product.image_credits,
      'daily_copy_limit', v_product.daily_copy_limit,
      'library_capacity', v_product.library_capacity,
      'test_purchase', true
    ), now()
  );

  if v_product.kind = 'subscription' then
    -- A test subscription replaces an existing live subscription so the
    -- current entitlement is unambiguous during administrator QA.
    update public.member_subscriptions
    set status = 'expired', current_period_end = least(current_period_end, v_start)
    where user_id = p_user_id and status in ('active', 'past_due');
    update public.member_entitlements
    set expires_at = least(expires_at, v_start)
    where user_id = p_user_id and kind = 'subscription' and expires_at > v_start;
    v_entitlement_end := v_start + make_interval(months => v_product.billing_interval_months);
    v_credit_end := v_entitlement_end + make_interval(months => v_product.billing_interval_months);
    v_subscription_id := gen_random_uuid();
    insert into public.member_subscriptions (
      id, user_id, product_code, status, provider_subscription_reference,
      current_period_start, current_period_end, latest_order_id
    ) values (
      v_subscription_id, p_user_id, v_product.code, 'active',
      'admin-test-subscription:' || v_order_id::text, v_start, v_entitlement_end, v_order_id
    );
  else
    v_entitlement_end := v_start + make_interval(days => v_product.credit_valid_days);
    v_credit_end := v_entitlement_end;
  end if;

  insert into public.member_entitlements (
    id, user_id, kind, daily_copy_limit, starts_at, expires_at, external_reference,
    note, created_by, product_code, order_id, library_capacity
  ) values (
    v_entitlement_id, p_user_id, v_product.kind, v_product.daily_copy_limit,
    v_start, v_entitlement_end, 'admin-test-entitlement:' || v_order_id::text,
    v_note, auth.uid(), v_product.code, v_order_id, v_product.library_capacity
  );
  insert into public.image_credit_grants (
    user_id, entitlement_id, total_credits, remaining_credits, starts_at, expires_at,
    external_reference, source_kind, product_code, order_id, subscription_id
  ) values (
    p_user_id, v_entitlement_id, v_product.image_credits, v_product.image_credits,
    v_start, v_credit_end, 'admin-test-grant:' || v_order_id::text, v_product.kind,
    v_product.code, v_order_id, v_subscription_id
  );
  return query select v_order_id, v_product.code, v_product.kind, v_product.image_credits,
    v_entitlement_end, v_product.daily_copy_limit, v_product.library_capacity,
    v_credit_end, case when v_product.kind = 'subscription' then v_entitlement_end else null end;
end;
$$;

revoke all on function public.get_my_effective_entitlement() from public, anon;
revoke all on function public.get_my_membership_status() from public, anon;
revoke all on function public.get_my_copy_usage_status() from public, anon;
revoke all on function public.reserve_copy_generation(uuid) from public, anon;
revoke all on function public.get_my_library_status() from public, anon;
revoke all on function public.create_library_project_slot(uuid, text, jsonb) from public, anon;
revoke all on function public.get_my_credit_balance_for_user(uuid) from public, anon, authenticated;
revoke all on function public.admin_grant_test_purchase(uuid, text, text) from public, anon;
grant execute on function public.get_my_effective_entitlement() to authenticated;
grant execute on function public.get_my_membership_status() to authenticated;
grant execute on function public.get_my_copy_usage_status() to authenticated;
grant execute on function public.reserve_copy_generation(uuid) to authenticated;
grant execute on function public.get_my_library_status() to authenticated;
grant execute on function public.create_library_project_slot(uuid, text, jsonb) to authenticated;
grant execute on function public.admin_adjust_member_credits(uuid, integer, text) to authenticated;
grant execute on function public.admin_list_members(text) to authenticated;
grant execute on function public.admin_grant_test_purchase(uuid, text, text) to authenticated;

notify pgrst, 'reload schema';
