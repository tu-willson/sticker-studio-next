-- Fixed, searchable marketplace tags.  Publishers choose one to three tags
-- from the app's controlled catalog; members can filter without free-text tag
-- fragmentation.

alter table public.shared_templates
  add column if not exists tags text[] not null default array['未分類']::text[];

-- Existing early-access templates predate categories.  Place them in one
-- explicit fixed bucket so they remain discoverable without inventing tags.
update public.shared_templates
set tags = array['其他主題']::text[]
where tags = array['未分類']::text[];

alter table public.shared_templates
  drop constraint if exists shared_templates_tags_count_check;
alter table public.shared_templates
  add constraint shared_templates_tags_count_check
  check (cardinality(tags) between 1 and 3);

drop function if exists public.reserve_shared_template(uuid, uuid, text, text, integer);
drop function if exists public.reserve_shared_template(uuid, uuid, text, text, integer, text[]);

create function public.reserve_shared_template(
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
  v_entitlement record;
  v_limit integer := 0;
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
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_template_id is null or p_source_reel_id is null
     or char_length(v_title) not between 3 and 80
     or char_length(v_description) > 300
     or p_apply_cost not between 1 and 20
     or cardinality(v_tags) not between 1 and 3
     or exists (select 1 from unnest(v_tags) tag where not (tag = any(v_allowed_tags))) then
    raise exception 'invalid_shared_template' using errcode = '22023';
  end if;

  select * into v_entitlement from public.get_my_effective_entitlement();
  if found and v_entitlement.entitlement_kind = 'subscription' then
    case v_entitlement.product_code
      when 'basic_monthly' then v_limit := 10;
      when 'standard_monthly' then v_limit := 15;
      when 'advanced_monthly' then v_limit := 20;
    end case;
  end if;
  if v_limit = 0 then
    raise exception 'shared_templates_subscription_required' using errcode = '42501';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 47));
  select * into v_reel
  from public.creator_reels r
  where r.id = p_source_reel_id and r.owner_id = v_user and r.status = 'collected'
  for update;
  if not found then
    raise exception 'creator_reel_not_collected' using errcode = 'P0002';
  end if;

  delete from public.shared_templates t
  where t.owner_id = v_user and t.status = 'uploading'
    and t.created_at < now() - interval '30 minutes';

  if exists (
    select 1 from public.shared_templates t
    where t.owner_id = v_user and t.source_reel_id = p_source_reel_id
      and t.status in ('uploading', 'published')
  ) then
    raise exception 'shared_template_already_exists' using errcode = '23505';
  end if;

  select count(*)::integer into v_count
  from public.shared_templates t
  where t.owner_id = v_user and t.status in ('uploading', 'published');
  if v_count >= v_limit then
    raise exception 'shared_template_publish_limit' using errcode = '23514';
  end if;

  v_prefix := v_user::text || '/' || p_template_id::text || '/';
  insert into public.shared_templates (
    id, owner_id, source_reel_id, title, description, apply_cost, tags, status,
    snapshot, preview_path, style_reference_path
  ) values (
    p_template_id, v_user, v_reel.id, v_title, v_description, p_apply_cost, v_tags,
    'uploading', v_reel.snapshot, v_prefix || 'preview.webp',
    case when v_reel.style_reference_path is not null
      then v_prefix || 'style-reference.webp' else null end
  );

  return query select p_template_id, v_prefix || 'preview.webp',
    case when v_reel.style_reference_path is not null
      then v_prefix || 'style-reference.webp' else null end,
    v_limit, v_count + 1;
end;
$$;

create or replace function public.list_shared_template_marketplace(
  p_limit integer default 24,
  p_offset integer default 0,
  p_tags text[] default null,
  p_search text default null,
  p_sort text default 'newest'
)
returns table (
  template_id uuid,
  title text,
  description text,
  apply_cost integer,
  tags text[],
  preview_path text,
  has_style_reference boolean,
  published_at timestamptz,
  is_owner boolean,
  is_unlocked boolean,
  unlock_count bigint
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_tags text[] := array(select distinct trim(value) from unnest(coalesce(p_tags, array[]::text[])) value order by trim(value));
  v_search text := trim(coalesce(p_search, ''));
  v_sort text := case when p_sort in ('newest', 'popular', 'low_cost') then p_sort else 'newest' end;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  return query
  with market_templates as (
    select
      t.id as template_id,
      t.title,
      t.description,
      t.apply_cost,
      t.tags,
      t.preview_path,
      t.style_reference_path is not null as has_style_reference,
      t.published_at,
      t.owner_id = v_user as is_owner,
      (t.owner_id = v_user or exists (
        select 1 from public.shared_template_redemptions r
        where r.template_id = t.id and r.consumer_id = v_user
      )) as is_unlocked,
      count(r.id) filter (where r.consumer_id <> t.owner_id) as unlock_count
    from public.shared_templates t
    left join public.shared_template_redemptions r on r.template_id = t.id
    where t.status = 'published'
      and (cardinality(v_tags) = 0 or t.tags && v_tags)
      and (v_search = '' or t.title ilike '%' || v_search || '%' or t.description ilike '%' || v_search || '%')
    group by t.id
  )
  select * from market_templates
  order by
    case when v_sort = 'popular' then unlock_count else 0 end desc,
    case when v_sort = 'low_cost' then apply_cost else 0 end asc,
    case when v_sort = 'newest' then published_at end desc,
    template_id desc
  limit greatest(1, least(coalesce(p_limit, 24), 48))
  offset greatest(0, coalesce(p_offset, 0));
end;
$$;

revoke all on function public.reserve_shared_template(uuid, uuid, text, text, integer, text[]) from public, anon;
revoke all on function public.list_shared_template_marketplace(integer, integer, text[], text, text) from public, anon;
grant execute on function public.reserve_shared_template(uuid, uuid, text, text, integer, text[]) to authenticated;
grant execute on function public.list_shared_template_marketplace(integer, integer, text[], text, text) to authenticated;

notify pgrst, 'reload schema';
