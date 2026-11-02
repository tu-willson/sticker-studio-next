-- Creator Reels are a universal signed-in feature: every member receives the
-- same rolling 20-reel, 15-day automatic shelf.  Creative-pack limits only
-- govern collected reels and marketplace publication.  The 7-generation pack
-- is a compact creative pack during its own 30-day validity.

update public.commerce_products
set daily_copy_limit = 7,
    library_capacity = 7,
    credit_valid_days = 30,
    metadata = coalesce(metadata, '{}'::jsonb) ||
      '{"billing_model":"one_time_30_day_pack","auto_renew":false,"marketplace_eligible":true,"collected_reel_limit":7,"publish_template_limit":3}'::jsonb,
    updated_at = now()
where code = 'credit_pack_7';

-- The highest active purchase decides the member's current non-credit rights.
-- The separate image-credit lots remain independent and retain their own
-- expiry/order of consumption.
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
    and e.product_code in ('basic_monthly', 'standard_monthly', 'advanced_monthly', 'credit_pack_7')
    and e.starts_at <= now() and e.expires_at > now()
  order by case e.product_code
      when 'advanced_monthly' then 3
      when 'standard_monthly' then 2
      when 'basic_monthly' then 1
      when 'credit_pack_7' then 0
      else -1
    end desc,
    e.expires_at desc,
    e.created_at desc
  limit 1;
  if found then return; end if;

  return query select null::uuid, null::text, 'free'::text, 2, 2, null::timestamptz;
end;
$$;

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
  v_auto_limit integer := 20;
  v_collected_limit integer := 5;
  v_publish_limit integer := 0;
  v_marketplace boolean := false;
  v_active_count integer := 0;
  v_collected_count integer := 0;
  v_unlocked_count integer := 0;
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;

  select * into v_entitlement from public.get_my_effective_entitlement();
  if found then
    case v_entitlement.product_code
      when 'credit_pack_7' then
        v_marketplace := true; v_collected_limit := 7; v_publish_limit := 3;
      when 'basic_monthly' then
        v_marketplace := true; v_collected_limit := 20; v_publish_limit := 10;
      when 'standard_monthly' then
        v_marketplace := true; v_collected_limit := 30; v_publish_limit := 15;
      when 'advanced_monthly' then
        v_marketplace := true; v_collected_limit := 40; v_publish_limit := 20;
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
    v_auto_limit, 15, v_collected_limit, v_publish_limit, v_active_count,
    v_collected_count, v_unlocked_count, v_collected_count + v_unlocked_count;
end;
$$;

create or replace function public.reserve_creator_reel(
  p_reel_id uuid,
  p_source_project_id uuid,
  p_source_project_name text,
  p_snapshot jsonb,
  p_has_style_reference boolean default false
)
returns table (
  reel_id uuid,
  grid_path text,
  style_reference_path text,
  auto_reel_limit integer,
  active_auto_reel_count integer,
  auto_expires_at timestamptz,
  cleanup_reel_ids uuid[],
  cleanup_object_paths text[]
)
language plpgsql
security definer
set search_path = public, storage
as $$
declare
  v_user uuid := auth.uid();
  v_rights record;
  v_auto_limit integer := 20;
  v_active_count integer := 0;
  v_reel_number integer := 1;
  v_prefix text;
  v_grid_path text;
  v_style_path text;
  v_expires_at timestamptz;
  v_cleanup_ids uuid[] := array[]::uuid[];
  v_cleanup_paths text[] := array[]::text[];
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  if p_reel_id is null or p_source_project_id is null
     or jsonb_typeof(coalesce(p_snapshot, '{}'::jsonb)) <> 'object' then
    raise exception 'invalid_creator_reel' using errcode = '22023';
  end if;

  select * into v_rights from public.get_my_creator_reel_library_status();
  v_auto_limit := v_rights.auto_reel_limit;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 41));

  update public.creator_reels r
  set status = 'expired', completed_at = coalesce(r.completed_at, now())
  where r.owner_id = v_user and r.status = 'active'
    and r.auto_expires_at is not null and r.auto_expires_at <= now();

  select count(*)::integer into v_active_count from public.creator_reels r
  where r.owner_id = v_user and r.status = 'active'
    and r.auto_expires_at is not null and r.auto_expires_at > now();

  if v_active_count >= v_auto_limit then
    with oldest as (
      select r.id from public.creator_reels r
      where r.owner_id = v_user and r.status = 'active'
        and r.auto_expires_at is not null
      order by r.created_at asc
      limit v_active_count - v_auto_limit + 1
      for update
    )
    update public.creator_reels r set status = 'expired'
    from oldest where r.id = oldest.id;
  end if;

  select coalesce(max(r.reel_number), 0) + 1 into v_reel_number
  from public.creator_reels r
  where r.owner_id = v_user and r.source_project_id = p_source_project_id;

  v_prefix := v_user::text || '/' || p_reel_id::text || '/';
  v_grid_path := v_prefix || 'grid.webp';
  v_style_path := case when p_has_style_reference then v_prefix || 'style-reference.webp' else null end;
  v_expires_at := now() + interval '15 days';

  insert into public.creator_reels (
    id, owner_id, source_project_id, source_project_name, reel_number,
    status, snapshot, grid_path, style_reference_path, auto_expires_at
  ) values (
    p_reel_id, v_user, p_source_project_id,
    left(trim(coalesce(p_source_project_name, '')), 80), v_reel_number,
    'uploading', p_snapshot, v_grid_path, v_style_path, v_expires_at
  );

  select coalesce(array_agg(r.id order by r.created_at asc), array[]::uuid[]),
    coalesce(array_agg(r.grid_path order by r.created_at asc)
      || array_remove(array_agg(r.style_reference_path order by r.created_at asc), null),
      array[]::text[])
  into v_cleanup_ids, v_cleanup_paths
  from public.creator_reels r
  where r.owner_id = v_user and r.status = 'expired';

  return query select p_reel_id, v_grid_path, v_style_path, v_auto_limit,
    v_active_count + 1, v_expires_at, v_cleanup_ids, v_cleanup_paths;
end;
$$;

-- Existing automatic reels join the same retention schedule.  This only marks
-- expired database rows; object cleanup remains handled by normal reservation.
update public.creator_reels
set status = 'expired', completed_at = coalesce(completed_at, now())
where status = 'active' and created_at + interval '15 days' <= now();

update public.creator_reels
set auto_expires_at = created_at + interval '15 days'
where status = 'active';

revoke all on function public.get_my_effective_entitlement() from public, anon;
grant execute on function public.get_my_effective_entitlement() to authenticated;
revoke all on function public.get_my_creator_reel_library_status() from public, anon;
grant execute on function public.get_my_creator_reel_library_status() to authenticated;
revoke all on function public.reserve_creator_reel(uuid, uuid, text, jsonb, boolean) from public, anon;
grant execute on function public.reserve_creator_reel(uuid, uuid, text, jsonb, boolean) to authenticated;

notify pgrst, 'reload schema';
