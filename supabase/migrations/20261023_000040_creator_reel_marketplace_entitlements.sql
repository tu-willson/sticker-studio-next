-- Creator Reels are available to every signed-in member.  Marketplace access
-- is unlocked by a 7-credit pack or an active subscription.  Collected reels
-- and redeemed templates consume one shared private-library quota.

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
language plpgsql
security definer
set search_path = public
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
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  select * into v_entitlement from public.get_my_effective_entitlement();
  if found and v_entitlement.entitlement_kind = 'subscription' then
    v_marketplace := true;
    case v_entitlement.product_code
      when 'basic_monthly' then
        v_auto_limit := 20; v_collected_limit := 20; v_publish_limit := 10;
      when 'standard_monthly' then
        v_auto_limit := 30; v_collected_limit := 30; v_publish_limit := 15;
      when 'advanced_monthly' then
        v_auto_limit := 40; v_collected_limit := 40; v_publish_limit := 20;
    end case;
  elsif found and v_entitlement.product_code = 'credit_pack_7' then
    v_marketplace := true;
    v_auto_limit := 7; v_collected_limit := 5; v_publish_limit := 3;
  end if;

  select count(*)::integer into v_active_count
  from public.creator_reels r
  where r.owner_id = v_user
    and r.status = 'active'
    and r.auto_expires_at is not null
    and r.auto_expires_at > now();

  select count(*)::integer into v_collected_count
  from public.creator_reels r
  where r.owner_id = v_user and r.status = 'collected';

  select count(*)::integer into v_unlocked_count
  from public.shared_template_redemptions r
  join public.shared_templates t on t.id = r.template_id
  where r.consumer_id = v_user and t.owner_id <> v_user;

  return query select
    true,
    v_marketplace,
    coalesce(v_entitlement.product_code, 'free'),
    v_auto_limit,
    10,
    v_collected_limit,
    v_publish_limit,
    v_active_count,
    v_collected_count,
    v_unlocked_count,
    v_collected_count + v_unlocked_count;
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
  v_auto_limit integer := 0;
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

  select count(*)::integer into v_active_count
  from public.creator_reels r
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
  v_expires_at := now() + interval '10 days';

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

create or replace function public.collect_creator_reel(p_reel_id uuid)
returns table (collected_reel_count integer, collected_reel_limit integer)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_rights record;
  v_shared_count integer := 0;
  v_reel public.creator_reels%rowtype;
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  if p_reel_id is null then raise exception 'invalid_creator_reel' using errcode = '22023'; end if;
  select * into v_rights from public.get_my_creator_reel_library_status();
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 43));
  select * into v_reel from public.creator_reels r
  where r.id = p_reel_id and r.owner_id = v_user for update;
  if not found or v_reel.status <> 'active' or v_reel.auto_expires_at is null
     or v_reel.auto_expires_at <= now() then
    raise exception 'creator_reel_not_collectable' using errcode = 'P0002';
  end if;
  select count(*)::integer into v_shared_count
  from public.creator_reels r where r.owner_id = v_user and r.status = 'collected';
  select v_shared_count + count(*)::integer into v_shared_count
  from public.shared_template_redemptions redemption
  join public.shared_templates template on template.id = redemption.template_id
  where redemption.consumer_id = v_user and template.owner_id <> v_user;
  if v_shared_count >= v_rights.collected_reel_limit then
    raise exception 'creator_reel_collection_limit' using errcode = '23514';
  end if;
  update public.creator_reels r set status = 'collected', auto_expires_at = null
  where r.id = v_reel.id and r.owner_id = v_user;
  return query select v_shared_count + 1, v_rights.collected_reel_limit;
end;
$$;

create or replace function public.reserve_shared_template(
  p_template_id uuid,
  p_source_reel_id uuid,
  p_title text,
  p_description text,
  p_apply_cost integer,
  p_tags text[]
)
returns table (
  template_id uuid,
  preview_path text,
  style_reference_path text,
  publish_template_limit integer,
  published_template_count integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_rights record;
  v_count integer := 0;
  v_reel public.creator_reels%rowtype;
  v_title text := trim(coalesce(p_title, ''));
  v_description text := trim(coalesce(p_description, ''));
  v_tags text[] := array(select distinct trim(value) from unnest(coalesce(p_tags, array[]::text[])) value order by trim(value));
  v_prefix text;
  v_allowed_tags constant text[] := array[
    '日常招呼', '可愛療癒', '搞笑吐槽', '心情情緒', '情侶曖昧',
    '親友互動', '職場同事', '校園學生', '家庭親子', '寵物日常',
    '節日祝福', '生日慶祝', '加油鼓勵', '感謝道歉', '美食生活',
    '旅行出遊', '運動健身', '商務客服', '自媒體創作', '其他主題'
  ];
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  if p_template_id is null or p_source_reel_id is null
     or char_length(v_title) not between 3 and 80 or char_length(v_description) > 300
     or p_apply_cost not between 1 and 20 or cardinality(v_tags) not between 1 and 3
     or exists (select 1 from unnest(v_tags) tag where not (tag = any(v_allowed_tags))) then
    raise exception 'invalid_shared_template' using errcode = '22023';
  end if;
  select * into v_rights from public.get_my_creator_reel_library_status();
  if not v_rights.marketplace_eligible or v_rights.publish_template_limit = 0 then
    raise exception 'shared_templates_marketplace_required' using errcode = '42501';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 47));
  select * into v_reel from public.creator_reels r
  where r.id = p_source_reel_id and r.owner_id = v_user and r.status = 'collected'
  for update;
  if not found then raise exception 'creator_reel_not_collected' using errcode = 'P0002'; end if;
  delete from public.shared_templates t where t.owner_id = v_user
    and t.status = 'uploading' and t.created_at < now() - interval '30 minutes';
  if exists (select 1 from public.shared_templates t where t.owner_id = v_user
    and t.source_reel_id = p_source_reel_id and t.status in ('uploading', 'published')) then
    raise exception 'shared_template_already_exists' using errcode = '23505';
  end if;
  select count(*)::integer into v_count from public.shared_templates t
  where t.owner_id = v_user and t.status in ('uploading', 'published');
  if v_count >= v_rights.publish_template_limit then
    raise exception 'shared_template_publish_limit' using errcode = '23514';
  end if;
  v_prefix := v_user::text || '/' || p_template_id::text || '/';
  insert into public.shared_templates (
    id, owner_id, source_reel_id, title, description, apply_cost, tags, status,
    snapshot, preview_path, style_reference_path
  ) values (
    p_template_id, v_user, v_reel.id, v_title, v_description, p_apply_cost, v_tags,
    'uploading', v_reel.snapshot, v_prefix || 'preview.webp',
    case when v_reel.style_reference_path is not null then v_prefix || 'style-reference.webp' else null end
  );
  return query select p_template_id, v_prefix || 'preview.webp',
    case when v_reel.style_reference_path is not null then v_prefix || 'style-reference.webp' else null end,
    v_rights.publish_template_limit, v_count + 1;
end;
$$;

create or replace function public.redeem_shared_template(
  p_template_id uuid,
  p_idempotency_key uuid
)
returns table (
  template_id uuid,
  title text,
  charged_cost integer,
  remaining_credits integer,
  template_snapshot jsonb,
  style_reference_path text,
  already_unlocked boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_rights record;
  v_template public.shared_templates%rowtype;
  v_existing public.shared_template_redemptions%rowtype;
  v_balance integer := 0;
  v_remaining integer := 0;
  v_grant record;
  v_charge integer := 0;
  v_shared_count integer := 0;
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  if p_template_id is null or p_idempotency_key is null then
    raise exception 'invalid_shared_template_redemption' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 53));
  select * into v_existing from public.shared_template_redemptions r
  where r.consumer_id = v_user and r.template_id = p_template_id for update;
  if found then
    return query select v_existing.template_id,
      (select t.title from public.shared_templates t where t.id = v_existing.template_id),
      0, public.get_my_credit_balance_for_user(v_user), v_existing.snapshot,
      v_existing.style_reference_path, true;
    return;
  end if;
  select * into v_rights from public.get_my_creator_reel_library_status();
  if not v_rights.marketplace_eligible then
    raise exception 'shared_templates_marketplace_required' using errcode = '42501';
  end if;
  select * into v_template from public.shared_templates t
  where t.id = p_template_id and t.status = 'published' for update;
  if not found then raise exception 'shared_template_not_available' using errcode = 'P0002'; end if;
  if v_template.owner_id <> v_user then
    select count(*)::integer into v_shared_count from public.creator_reels r
    where r.owner_id = v_user and r.status = 'collected';
    select v_shared_count + count(*)::integer into v_shared_count
    from public.shared_template_redemptions redemption
    join public.shared_templates template on template.id = redemption.template_id
    where redemption.consumer_id = v_user and template.owner_id <> v_user;
    if v_shared_count >= v_rights.collected_reel_limit then
      raise exception 'shared_template_collection_limit' using errcode = '23514';
    end if;
    v_charge := v_template.apply_cost;
    v_balance := public.get_my_credit_balance_for_user(v_user);
    if v_balance < v_charge then raise exception 'shared_template_insufficient_credits' using errcode = '22023'; end if;
    v_remaining := v_charge;
    for v_grant in select g.id, g.remaining_credits from public.image_credit_grants g
      where g.user_id = v_user and g.starts_at <= now() and g.expires_at > now()
        and g.remaining_credits > 0
      order by case when g.source_kind = 'template_reward' then 1 else 0 end,
        g.expires_at asc, g.created_at asc for update
    loop
      exit when v_remaining = 0;
      update public.image_credit_grants g set remaining_credits = g.remaining_credits - least(g.remaining_credits, v_remaining)
      where g.id = v_grant.id;
      v_remaining := v_remaining - least(v_grant.remaining_credits, v_remaining);
    end loop;
    insert into public.image_credit_grants (
      user_id, total_credits, remaining_credits, starts_at, expires_at,
      external_reference, source_kind
    ) values (
      v_template.owner_id, v_charge, v_charge, now(), now() + interval '90 days',
      'template-reward:' || p_template_id::text || ':' || v_user::text, 'template_reward'
    );
  end if;
  insert into public.shared_template_redemptions (
    template_id, consumer_id, idempotency_key, charged_cost, snapshot, style_reference_path
  ) values (
    v_template.id, v_user, p_idempotency_key, v_charge, v_template.snapshot, v_template.style_reference_path
  );
  return query select v_template.id, v_template.title, v_charge,
    public.get_my_credit_balance_for_user(v_user), v_template.snapshot,
    v_template.style_reference_path, false;
end;
$$;

create or replace function public.list_my_unlocked_shared_templates(
  p_limit integer default 5,
  p_offset integer default 0
)
returns table (
  template_id uuid,
  title text,
  description text,
  tags text[],
  preview_path text,
  has_style_reference boolean,
  unlocked_at timestamptz,
  total_count bigint
)
language sql
stable
security definer
set search_path = public
as $$
  select
    t.id, t.title, t.description, t.tags, t.preview_path,
    t.style_reference_path is not null, r.created_at,
    count(*) over ()
  from public.shared_template_redemptions r
  join public.shared_templates t on t.id = r.template_id
  where r.consumer_id = auth.uid() and t.owner_id <> auth.uid()
  order by r.created_at desc, t.id desc
  limit greatest(1, least(coalesce(p_limit, 5), 12))
  offset greatest(0, coalesce(p_offset, 0));
$$;

revoke all on function public.get_my_creator_reel_library_status() from public, anon;
revoke all on function public.collect_creator_reel(uuid) from public, anon;
revoke all on function public.reserve_creator_reel(uuid, uuid, text, jsonb, boolean) from public, anon;
revoke all on function public.reserve_shared_template(uuid, uuid, text, text, integer, text[]) from public, anon;
revoke all on function public.redeem_shared_template(uuid, uuid) from public, anon;
revoke all on function public.list_my_unlocked_shared_templates(integer, integer) from public, anon;
grant execute on function public.get_my_creator_reel_library_status() to authenticated;
grant execute on function public.collect_creator_reel(uuid) to authenticated;
grant execute on function public.reserve_creator_reel(uuid, uuid, text, jsonb, boolean) to authenticated;
grant execute on function public.reserve_shared_template(uuid, uuid, text, text, integer, text[]) to authenticated;
grant execute on function public.redeem_shared_template(uuid, uuid) to authenticated;
grant execute on function public.list_my_unlocked_shared_templates(integer, integer) to authenticated;

notify pgrst, 'reload schema';
