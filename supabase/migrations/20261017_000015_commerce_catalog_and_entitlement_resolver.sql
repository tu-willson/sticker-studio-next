-- Sticker Studio Next: paid-product catalog and authoritative entitlement resolver.
--
-- This is deliberately payment-provider agnostic.  A future verified webhook
-- is the only code allowed to mark an order as paid.  Browser clients can
-- read their own status but cannot create paid
-- orders, subscriptions, grants, or entitlements.

create table public.commerce_products (
  code text primary key check (code ~ '^[a-z0-9_]{3,48}$'),
  kind text not null check (kind in ('credit_pack', 'subscription')),
  display_name text not null check (char_length(trim(display_name)) between 1 and 60),
  price_twd integer not null check (price_twd between 1 and 100000),
  image_credits integer not null check (image_credits between 1 and 10000),
  daily_copy_limit integer not null check (daily_copy_limit between 1 and 100),
  library_capacity integer not null check (library_capacity between 1 and 1000),
  credit_valid_days integer check (credit_valid_days between 1 and 366),
  billing_interval_months integer check (billing_interval_months between 1 and 24),
  is_active boolean not null default true,
  sort_order integer not null default 0,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (
    (kind = 'credit_pack' and credit_valid_days is not null and billing_interval_months is null)
    or
    (kind = 'subscription' and credit_valid_days is null and billing_interval_months is not null)
  )
);

create trigger commerce_products_set_updated_at
before update on public.commerce_products
for each row execute function public.set_updated_at();

-- Product snapshots are immutable at payment time, so a later price change
-- never rewrites the amount or entitlement a member actually purchased.
create table public.commerce_orders (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete restrict,
  product_code text not null references public.commerce_products(code) on delete restrict,
  status text not null default 'pending'
    check (status in ('pending', 'paid', 'failed', 'cancelled', 'refunded')),
  payment_provider text not null default 'manual_test'
    check (char_length(trim(payment_provider)) between 2 and 40),
  provider_order_reference text,
  amount_twd integer not null check (amount_twd between 0 and 100000),
  product_snapshot jsonb not null default '{}'::jsonb,
  paid_at timestamptz,
  refunded_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (status <> 'paid' or paid_at is not null),
  check (refunded_at is null or status = 'refunded')
);

create unique index commerce_orders_provider_reference_unique
  on public.commerce_orders (payment_provider, provider_order_reference)
  where provider_order_reference is not null;
create index commerce_orders_user_created_idx
  on public.commerce_orders (user_id, created_at desc);

create trigger commerce_orders_set_updated_at
before update on public.commerce_orders
for each row execute function public.set_updated_at();

create table public.member_subscriptions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  product_code text not null references public.commerce_products(code) on delete restrict,
  status text not null check (status in ('active', 'cancelled', 'expired', 'past_due')),
  provider_subscription_reference text,
  current_period_start timestamptz not null,
  current_period_end timestamptz not null,
  cancel_at_period_end boolean not null default false,
  latest_order_id uuid references public.commerce_orders(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (current_period_end > current_period_start)
);

create unique index member_subscriptions_provider_reference_unique
  on public.member_subscriptions (provider_subscription_reference)
  where provider_subscription_reference is not null;
create unique index member_subscriptions_one_live_per_member_idx
  on public.member_subscriptions (user_id)
  where status in ('active', 'past_due');
create index member_subscriptions_user_period_idx
  on public.member_subscriptions (user_id, current_period_end desc);

create trigger member_subscriptions_set_updated_at
before update on public.member_subscriptions
for each row execute function public.set_updated_at();

alter table public.member_entitlements
  add column if not exists product_code text references public.commerce_products(code) on delete restrict,
  add column if not exists order_id uuid references public.commerce_orders(id) on delete set null,
  add column if not exists library_capacity integer check (library_capacity between 1 and 1000);

create unique index member_entitlements_order_unique
  on public.member_entitlements (order_id)
  where order_id is not null;
create index member_entitlements_product_active_idx
  on public.member_entitlements (user_id, kind, starts_at, expires_at desc);

alter table public.image_credit_grants
  add column if not exists remaining_credits integer,
  add column if not exists product_code text references public.commerce_products(code) on delete restrict,
  add column if not exists order_id uuid references public.commerce_orders(id) on delete set null,
  add column if not exists subscription_id uuid references public.member_subscriptions(id) on delete set null;

-- Existing beta grants are empty at the time this migration is introduced,
-- but initialize defensively before making the remaining balance mandatory.
update public.image_credit_grants
set remaining_credits = total_credits
where remaining_credits is null;

alter table public.image_credit_grants
  alter column remaining_credits set not null;

alter table public.image_credit_grants
  add constraint image_credit_grants_remaining_range
  check (remaining_credits between 0 and total_credits);

create unique index image_credit_grants_order_unique
  on public.image_credit_grants (order_id)
  where order_id is not null;
create index image_credit_grants_spendable_idx
  on public.image_credit_grants (user_id, expires_at, starts_at)
  where remaining_credits > 0;

alter table public.commerce_products enable row level security;
alter table public.commerce_orders enable row level security;
alter table public.member_subscriptions enable row level security;

revoke all on table public.commerce_products, public.commerce_orders,
  public.member_subscriptions from anon, authenticated;
grant select on table public.commerce_products, public.commerce_orders,
  public.member_subscriptions to authenticated;

create policy "commerce_products_select_active"
on public.commerce_products for select to authenticated
using (is_active);

create policy "commerce_orders_select_own"
on public.commerce_orders for select to authenticated
using (user_id = (select auth.uid()));

create policy "member_subscriptions_select_own"
on public.member_subscriptions for select to authenticated
using (user_id = (select auth.uid()));

-- The values below are the published business rules as of 2026-10-17.
-- A product may later be retired (is_active=false) without changing member
-- orders or grants that already exist.
insert into public.commerce_products (
  code, kind, display_name, price_twd, image_credits, daily_copy_limit,
  library_capacity, credit_valid_days, billing_interval_months, sort_order, metadata
) values
  ('basic_monthly', 'subscription', '基本方案', 99, 20, 15, 20, null, 1, 10,
    '{"monthly_image_cost_twd":40,"carryover_cycles":1}'::jsonb),
  ('standard_monthly', 'subscription', '標準方案', 149, 30, 20, 30, null, 1, 20,
    '{"monthly_image_cost_twd":60,"carryover_cycles":1}'::jsonb),
  ('advanced_monthly', 'subscription', '進階方案', 199, 40, 25, 40, null, 1, 30,
    '{"monthly_image_cost_twd":80,"carryover_cycles":1}'::jsonb),
  ('credit_pack_7', 'credit_pack', '7 點點數包', 49, 7, 7, 7, 30, null, 40,
    '{"estimated_image_cost_twd":14}'::jsonb)
on conflict (code) do update set
  kind = excluded.kind,
  display_name = excluded.display_name,
  price_twd = excluded.price_twd,
  image_credits = excluded.image_credits,
  daily_copy_limit = excluded.daily_copy_limit,
  library_capacity = excluded.library_capacity,
  credit_valid_days = excluded.credit_valid_days,
  billing_interval_months = excluded.billing_interval_months,
  sort_order = excluded.sort_order,
  metadata = excluded.metadata,
  updated_at = now();

-- One resolver prevents the copy and cloud-library paths from gradually
-- diverging.  Subscription always wins over a point pack.  If several valid
-- point packs exist, the most generous current pack wins; capacity is not
-- accumulated across packages.
create or replace function public.get_my_effective_entitlement()
returns table (
  entitlement_id uuid,
  product_code text,
  entitlement_kind text,
  daily_copy_limit integer,
  library_capacity integer,
  expires_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  return query
  select e.id, e.product_code, e.kind, e.daily_copy_limit,
         coalesce(e.library_capacity, 5), e.expires_at
  from public.member_entitlements e
  where e.user_id = v_user
    and e.kind = 'subscription'
    and e.starts_at <= now()
    and e.expires_at > now()
  order by e.expires_at desc, e.created_at desc
  limit 1;
  if found then return; end if;

  return query
  select e.id, e.product_code, e.kind, e.daily_copy_limit,
         coalesce(e.library_capacity, 5), e.expires_at
  from public.member_entitlements e
  where e.user_id = v_user
    and e.kind = 'credit_pack'
    and e.starts_at <= now()
    and e.expires_at > now()
  order by e.daily_copy_limit desc, coalesce(e.library_capacity, 5) desc,
           e.expires_at desc, e.created_at desc
  limit 1;
  if found then return; end if;

  return query select null::uuid, null::text, 'free'::text, 2, 5,
    null::timestamptz;
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
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  return query
  select e.product_code, e.entitlement_kind, e.daily_copy_limit,
         e.library_capacity, e.expires_at,
         (
           select s.current_period_end
           from public.member_subscriptions s
           where s.user_id = v_user and s.status in ('active', 'past_due')
           order by s.current_period_end desc
           limit 1
         )
  from public.get_my_effective_entitlement() e;
end;
$$;

create or replace function public.get_my_copy_usage_status()
returns table (
  usage_date date,
  daily_limit integer,
  used_count integer,
  remaining_count integer,
  entitlement_kind text,
  entitlement_expires_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_today date := (now() at time zone 'Asia/Taipei')::date;
  v_entitlement record;
  v_used integer := 0;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  select * into v_entitlement from public.get_my_effective_entitlement();
  select count(*)::integer into v_used
  from public.copy_generation_usage u
  where u.user_id = v_user and u.usage_date = v_today
    and u.status in ('reserved', 'succeeded');
  return query select v_today, v_entitlement.daily_copy_limit, v_used,
    greatest(v_entitlement.daily_copy_limit - v_used, 0),
    v_entitlement.entitlement_kind, v_entitlement.expires_at;
end;
$$;

create or replace function public.reserve_copy_generation(p_idempotency_key uuid)
returns table (
  job_id uuid,
  granted boolean,
  usage_date date,
  daily_limit integer,
  remaining_count integer,
  entitlement_kind text,
  entitlement_expires_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_today date := (now() at time zone 'Asia/Taipei')::date;
  v_entitlement record;
  v_existing public.copy_generation_usage%rowtype;
  v_used integer := 0;
  v_job_id uuid;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_idempotency_key is null then
    raise exception 'invalid_idempotency_key' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 2));
  update public.copy_generation_usage u
  set status = 'failed', completed_at = now(), failure_code = 'stale_timeout'
  where u.user_id = v_user and u.status = 'reserved'
    and u.created_at < now() - interval '10 minutes';

  select * into v_entitlement from public.get_my_effective_entitlement();
  select * into v_existing
  from public.copy_generation_usage u
  where u.user_id = v_user and u.idempotency_key = p_idempotency_key;
  if found then
    select count(*)::integer into v_used
    from public.copy_generation_usage u
    where u.user_id = v_user and u.usage_date = v_today
      and u.status in ('reserved', 'succeeded');
    return query select v_existing.id,
      v_existing.status in ('reserved', 'succeeded'), v_today,
      v_entitlement.daily_copy_limit,
      greatest(v_entitlement.daily_copy_limit - v_used, 0),
      v_entitlement.entitlement_kind, v_entitlement.expires_at;
    return;
  end if;

  select count(*)::integer into v_used
  from public.copy_generation_usage u
  where u.user_id = v_user and u.usage_date = v_today
    and u.status in ('reserved', 'succeeded');
  if v_used >= v_entitlement.daily_copy_limit then
    return query select null::uuid, false, v_today, v_entitlement.daily_copy_limit,
      0, v_entitlement.entitlement_kind, v_entitlement.expires_at;
    return;
  end if;

  v_job_id := gen_random_uuid();
  insert into public.copy_generation_usage (
    id, user_id, idempotency_key, usage_date, entitlement_id,
    daily_limit_snapshot, status
  ) values (
    v_job_id, v_user, p_idempotency_key, v_today,
    v_entitlement.entitlement_id, v_entitlement.daily_copy_limit, 'reserved'
  );
  return query select v_job_id, true, v_today, v_entitlement.daily_copy_limit,
    v_entitlement.daily_copy_limit - v_used - 1,
    v_entitlement.entitlement_kind, v_entitlement.expires_at;
end;
$$;

create or replace function public.get_my_library_status()
returns table (saved_count integer, library_capacity integer)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_entitlement record;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  select * into v_entitlement from public.get_my_effective_entitlement();
  return query select count(*)::integer, v_entitlement.library_capacity
  from public.library_projects lp
  where lp.owner_id = v_user and lp.status in ('uploading', 'active');
end;
$$;

drop function if exists public.create_library_project_slot(uuid, text, jsonb);
create function public.create_library_project_slot(
  p_project_id uuid,
  p_title text,
  p_metadata jsonb default '{}'::jsonb
)
returns table (project_id uuid, saved_count integer, library_capacity integer)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_entitlement record;
  v_capacity integer;
  v_count integer;
  v_title text := trim(coalesce(p_title, ''));
  v_prefix text;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_project_id is null or char_length(v_title) not between 1 and 80 then
    raise exception 'invalid_library_project' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 1));
  delete from public.library_projects
  where owner_id = v_user and status = 'uploading'
    and created_at < now() - interval '30 minutes';
  select * into v_entitlement from public.get_my_effective_entitlement();
  v_capacity := v_entitlement.library_capacity;
  select count(*)::integer into v_count
  from public.library_projects
  where owner_id = v_user and status in ('uploading', 'active');
  if v_count >= v_capacity then
    return query select null::uuid, v_count, v_capacity;
    return;
  end if;
  v_prefix := v_user::text || '/' || p_project_id::text || '/';
  insert into public.library_projects (
    id, owner_id, title, metadata, preview_path, sticker_prefix,
    source_photo_path, style_reference_path
  ) values (
    p_project_id, v_user, v_title, coalesce(p_metadata, '{}'::jsonb),
    v_prefix || 'preview.webp', v_prefix || 'stickers/',
    v_prefix || 'source-photo.webp', v_prefix || 'style-reference.webp'
  );
  return query select p_project_id, v_count + 1, v_capacity;
end;
$$;

-- No unauthenticated/payment-browser route can create paid orders or grant
-- rights.  Those future write functions stay server-only and will be added
-- together with a verified payment webhook.
revoke all on function public.get_my_effective_entitlement() from public, anon;
revoke all on function public.get_my_membership_status() from public, anon;
revoke all on function public.get_my_copy_usage_status() from public, anon;
revoke all on function public.reserve_copy_generation(uuid) from public, anon;
revoke all on function public.get_my_library_status() from public, anon;
revoke all on function public.create_library_project_slot(uuid, text, jsonb) from public, anon;
grant execute on function public.get_my_effective_entitlement() to authenticated;
grant execute on function public.get_my_membership_status() to authenticated;
grant execute on function public.get_my_copy_usage_status() to authenticated;
grant execute on function public.reserve_copy_generation(uuid) to authenticated;
grant execute on function public.get_my_library_status() to authenticated;
grant execute on function public.create_library_project_slot(uuid, text, jsonb) to authenticated;

notify pgrst, 'reload schema';
