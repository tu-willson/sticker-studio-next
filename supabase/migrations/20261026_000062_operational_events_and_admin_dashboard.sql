-- Sticker Studio Next: privacy-conscious operational events and the
-- administrator-only dashboard.  This is deliberately an audit/health layer:
-- it records outcomes and safe failure codes, never prompt text, photos, or
-- private creative settings.

create table if not exists public.operational_events (
  id uuid primary key default gen_random_uuid(),
  occurred_at timestamptz not null default now(),
  user_id uuid references auth.users(id) on delete set null,
  event_type text not null check (event_type in (
    'login', 'generation', 'cloud_save', 'creator_reel_save',
    'referral_redemption', 'marketplace_purchase', 'payment'
  )),
  outcome text not null check (outcome in ('succeeded', 'failed')),
  failure_code text check (
    failure_code is null or char_length(failure_code) between 1 and 100
  ),
  entity_id uuid,
  source text not null default 'database' check (source in ('app', 'database', 'payment')),
  is_test boolean not null default false,
  metadata jsonb not null default '{}'::jsonb
    check (jsonb_typeof(metadata) = 'object')
);

create index if not exists operational_events_occurred_idx
  on public.operational_events (occurred_at desc);
create index if not exists operational_events_type_outcome_time_idx
  on public.operational_events (event_type, outcome, occurred_at desc);
create index if not exists operational_events_user_time_idx
  on public.operational_events (user_id, occurred_at desc)
  where user_id is not null;

alter table public.operational_events enable row level security;
revoke all on table public.operational_events from anon, authenticated;

-- App-originated events are intentionally narrow.  A member can only add an
-- event to their own timeline and cannot forge a payment, referral, or market
-- transaction; those are captured by database triggers below.
create or replace function public.record_my_operational_event(
  p_event_type text,
  p_outcome text default 'succeeded',
  p_failure_code text default null,
  p_metadata jsonb default '{}'::jsonb
)
returns boolean
language plpgsql security definer set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_event text := trim(coalesce(p_event_type, ''));
  v_outcome text := trim(coalesce(p_outcome, ''));
  v_metadata jsonb := coalesce(p_metadata, '{}'::jsonb);
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if v_event not in ('login', 'cloud_save', 'creator_reel_save')
     or v_outcome not in ('succeeded', 'failed')
     or jsonb_typeof(v_metadata) <> 'object' then
    raise exception 'invalid_operational_event' using errcode = '22023';
  end if;

  insert into public.operational_events (
    user_id, event_type, outcome, failure_code, source, metadata
  ) values (
    v_user, v_event, v_outcome,
    nullif(left(trim(coalesce(p_failure_code, '')), 100), ''),
    'app', v_metadata
  );
  return true;
end;
$$;

create or replace function public.capture_generation_operational_event()
returns trigger
language plpgsql security definer set search_path = public
as $$
begin
  if new.status in ('succeeded', 'failed')
     and old.status is distinct from new.status then
    insert into public.operational_events (
      occurred_at, user_id, event_type, outcome, failure_code, entity_id, metadata
    ) values (
      coalesce(new.completed_at, now()), new.user_id, 'generation',
      case when new.status = 'succeeded' then 'succeeded' else 'failed' end,
      case when new.status = 'failed' then nullif(left(new.failure_code, 100), '') else null end,
      new.id, jsonb_build_object('api_mode', coalesce(new.api_mode, 'unknown'))
    );
  end if;
  return new;
end;
$$;

drop trigger if exists generation_jobs_capture_operational_event on public.generation_jobs;
create trigger generation_jobs_capture_operational_event
after update of status on public.generation_jobs
for each row execute function public.capture_generation_operational_event();

create or replace function public.capture_library_save_operational_event()
returns trigger
language plpgsql security definer set search_path = public
as $$
begin
  if old.status = 'uploading' and new.status = 'active' then
    insert into public.operational_events (
      occurred_at, user_id, event_type, outcome, entity_id, metadata
    ) values (
      coalesce(new.completed_at, now()), new.owner_id, 'cloud_save', 'succeeded',
      new.id, jsonb_build_object('operation', 'new_project')
    );
  end if;
  return new;
end;
$$;

drop trigger if exists library_projects_capture_operational_event on public.library_projects;
create trigger library_projects_capture_operational_event
after update of status on public.library_projects
for each row execute function public.capture_library_save_operational_event();

create or replace function public.capture_creator_reel_operational_event()
returns trigger
language plpgsql security definer set search_path = public
as $$
begin
  if old.status = 'uploading' and new.status = 'active' then
    insert into public.operational_events (
      occurred_at, user_id, event_type, outcome, entity_id, metadata
    ) values (
      coalesce(new.completed_at, now()), new.owner_id, 'creator_reel_save', 'succeeded',
      new.id, jsonb_build_object('operation', 'automatic_reel')
    );
  end if;
  return new;
end;
$$;

drop trigger if exists creator_reels_capture_operational_event on public.creator_reels;
create trigger creator_reels_capture_operational_event
after update of status on public.creator_reels
for each row execute function public.capture_creator_reel_operational_event();

create or replace function public.capture_referral_operational_event()
returns trigger
language plpgsql security definer set search_path = public
as $$
begin
  insert into public.operational_events (
    occurred_at, user_id, event_type, outcome, entity_id, metadata
  ) values (
    new.redeemed_at, new.redeemer_user_id, 'referral_redemption', 'succeeded',
    new.id, jsonb_build_object('reward_credits', new.reward_credits, 'code_id', new.code_id)
  );
  return new;
end;
$$;

drop trigger if exists referral_redemptions_capture_operational_event on public.referral_code_redemptions;
create trigger referral_redemptions_capture_operational_event
after insert on public.referral_code_redemptions
for each row execute function public.capture_referral_operational_event();

create or replace function public.capture_marketplace_purchase_operational_event()
returns trigger
language plpgsql security definer set search_path = public
as $$
begin
  insert into public.operational_events (
    occurred_at, user_id, event_type, outcome, entity_id, metadata
  ) values (
    new.created_at, new.consumer_id, 'marketplace_purchase', 'succeeded',
    new.template_id, jsonb_build_object('charged_cost', new.charged_cost, 'redemption_id', new.id)
  );
  return new;
end;
$$;

drop trigger if exists shared_template_redemptions_capture_operational_event on public.shared_template_redemptions;
create trigger shared_template_redemptions_capture_operational_event
after insert on public.shared_template_redemptions
for each row execute function public.capture_marketplace_purchase_operational_event();

create or replace function public.capture_payment_operational_event()
returns trigger
language plpgsql security definer set search_path = public
as $$
declare
  v_changed boolean := tg_op = 'INSERT' or old.status is distinct from new.status;
begin
  if v_changed and new.status in ('paid', 'failed', 'cancelled', 'refunded') then
    insert into public.operational_events (
      occurred_at, user_id, event_type, outcome, failure_code, entity_id, source, is_test, metadata
    ) values (
      case when new.status = 'paid' then coalesce(new.paid_at, now()) else now() end,
      new.user_id, 'payment', case when new.status = 'paid' then 'succeeded' else 'failed' end,
      case when new.status = 'paid' then null else new.status end,
      new.id, 'payment', new.payment_provider = 'admin_test',
      jsonb_build_object('product_code', new.product_code, 'amount_twd', new.amount_twd,
                         'payment_provider', new.payment_provider)
    );
  end if;
  return new;
end;
$$;

drop trigger if exists commerce_orders_capture_operational_event on public.commerce_orders;
create trigger commerce_orders_capture_operational_event
after insert or update of status on public.commerce_orders
for each row execute function public.capture_payment_operational_event();

create or replace function public.admin_get_operational_dashboard(
  p_period text default 'week'
)
returns table (
  range_start timestamptz,
  range_end timestamptz,
  signed_in_members integer,
  new_members integer,
  generation_succeeded integer,
  generation_failed integer,
  cloud_save_succeeded integer,
  cloud_save_failed integer,
  reel_save_succeeded integer,
  reel_save_failed integer,
  referral_redemptions integer,
  marketplace_purchases integer,
  marketplace_credits integer,
  paid_members integer,
  test_members integer,
  payment_failed integer,
  pending_reviews integer,
  report_count integer,
  expiring_referral_codes integer
)
language plpgsql security definer set search_path = public
as $$
declare
  v_start timestamptz;
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  v_start := case p_period
    when 'day' then now() - interval '1 day'
    when 'month' then now() - interval '30 days'
    else now() - interval '7 days'
  end;

  return query select
    v_start, now(),
    (select count(distinct e.user_id)::integer from public.operational_events e
      where e.event_type = 'login' and e.occurred_at >= v_start),
    (select count(*)::integer from public.profiles p where p.created_at >= v_start),
    (select count(*)::integer from public.generation_jobs j
      where j.status = 'succeeded' and j.completed_at >= v_start),
    (select count(*)::integer from public.generation_jobs j
      where j.status = 'failed' and j.completed_at >= v_start),
    (select count(*)::integer from public.operational_events e
      where e.event_type = 'cloud_save' and e.outcome = 'succeeded' and e.occurred_at >= v_start),
    (select count(*)::integer from public.operational_events e
      where e.event_type = 'cloud_save' and e.outcome = 'failed' and e.occurred_at >= v_start),
    (select count(*)::integer from public.operational_events e
      where e.event_type = 'creator_reel_save' and e.outcome = 'succeeded' and e.occurred_at >= v_start),
    (select count(*)::integer from public.operational_events e
      where e.event_type = 'creator_reel_save' and e.outcome = 'failed' and e.occurred_at >= v_start),
    (select count(*)::integer from public.referral_code_redemptions r where r.redeemed_at >= v_start),
    (select count(*)::integer from public.shared_template_redemptions r where r.created_at >= v_start),
    (select coalesce(sum(r.charged_cost), 0)::integer from public.shared_template_redemptions r where r.created_at >= v_start),
    (select count(distinct s.user_id)::integer from public.member_subscriptions s
      join public.commerce_orders o on o.id = s.latest_order_id
      where s.status = 'active' and s.current_period_end > now() and o.payment_provider <> 'admin_test'),
    (select count(distinct s.user_id)::integer from public.member_subscriptions s
      join public.commerce_orders o on o.id = s.latest_order_id
      where s.status = 'active' and s.current_period_end > now() and o.payment_provider = 'admin_test'),
    (select count(*)::integer from public.commerce_orders o
      where o.status in ('failed', 'cancelled') and o.payment_provider <> 'admin_test' and o.updated_at >= v_start),
    (select count(*)::integer from public.shared_template_moderation_queue q where q.review_status = 'pending_review'),
    (select count(*)::integer from public.shared_template_reports r where r.created_at >= v_start),
    (select count(*)::integer from public.referral_codes c
      where c.is_active and c.expires_at is not null and c.expires_at > now()
        and c.expires_at <= now() + interval '7 days');
end;
$$;

create or replace function public.admin_list_operational_trend(
  p_period text default 'week'
)
returns table (
  bucket_at timestamptz,
  signed_in_members integer,
  generation_succeeded integer,
  generation_failed integer,
  cloud_save_failed integer,
  referral_redemptions integer,
  marketplace_purchases integer,
  reports integer
)
language plpgsql security definer set search_path = public
as $$
declare
  v_start timestamptz;
  v_step interval;
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  if p_period = 'day' then
    v_start := date_trunc('hour', now()) - interval '23 hours'; v_step := interval '1 hour';
  elsif p_period = 'month' then
    v_start := date_trunc('day', now()) - interval '29 days'; v_step := interval '1 day';
  else
    v_start := date_trunc('day', now()) - interval '6 days'; v_step := interval '1 day';
  end if;

  return query
  select series.bucket_at,
    (select count(distinct e.user_id)::integer from public.operational_events e
      where e.event_type = 'login' and e.occurred_at >= series.bucket_at and e.occurred_at < series.bucket_at + v_step),
    (select count(*)::integer from public.generation_jobs j
      where j.status = 'succeeded' and j.completed_at >= series.bucket_at and j.completed_at < series.bucket_at + v_step),
    (select count(*)::integer from public.generation_jobs j
      where j.status = 'failed' and j.completed_at >= series.bucket_at and j.completed_at < series.bucket_at + v_step),
    (select count(*)::integer from public.operational_events e
      where e.event_type = 'cloud_save' and e.outcome = 'failed'
        and e.occurred_at >= series.bucket_at and e.occurred_at < series.bucket_at + v_step),
    (select count(*)::integer from public.referral_code_redemptions r
      where r.redeemed_at >= series.bucket_at and r.redeemed_at < series.bucket_at + v_step),
    (select count(*)::integer from public.shared_template_redemptions r
      where r.created_at >= series.bucket_at and r.created_at < series.bucket_at + v_step),
    (select count(*)::integer from public.shared_template_reports r
      where r.created_at >= series.bucket_at and r.created_at < series.bucket_at + v_step)
  from generate_series(v_start, date_trunc(case when p_period = 'day' then 'hour' else 'day' end, now()), v_step) as series(bucket_at)
  order by series.bucket_at;
end;
$$;

create or replace function public.admin_list_recent_operational_failures(
  p_period text default 'week',
  p_limit integer default 30
)
returns table (
  occurred_at timestamptz,
  event_type text,
  failure_code text,
  source text,
  member_email text
)
language plpgsql security definer set search_path = public
as $$
declare
  v_start timestamptz;
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  v_start := case p_period
    when 'day' then now() - interval '1 day'
    when 'month' then now() - interval '30 days'
    else now() - interval '7 days'
  end;
  return query
  select e.occurred_at, e.event_type, coalesce(e.failure_code, 'unknown'), e.source,
    coalesce(p.email, '')
  from public.operational_events e
  left join public.profiles p on p.id = e.user_id
  where e.outcome = 'failed' and e.occurred_at >= v_start
  order by e.occurred_at desc
  limit greatest(1, least(coalesce(p_limit, 30), 100));
end;
$$;

revoke all on function public.record_my_operational_event(text, text, text, jsonb) from public, anon;
revoke all on function public.admin_get_operational_dashboard(text) from public, anon;
revoke all on function public.admin_list_operational_trend(text) from public, anon;
revoke all on function public.admin_list_recent_operational_failures(text, integer) from public, anon;
grant execute on function public.record_my_operational_event(text, text, text, jsonb) to authenticated;
grant execute on function public.admin_get_operational_dashboard(text) to authenticated;
grant execute on function public.admin_list_operational_trend(text) to authenticated;
grant execute on function public.admin_list_recent_operational_failures(text, integer) to authenticated;

notify pgrst, 'reload schema';
