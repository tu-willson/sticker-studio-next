-- A withdrawn listing is no longer public.  Its collected source reel may be
-- removed by the owner; the marketplace management row disappears with it.
-- Previously redeemed snapshots remain private so a buyer never loses an
-- already-unlocked template.

alter table public.shared_templates
  drop constraint if exists shared_templates_status_check;
alter table public.shared_templates
  add constraint shared_templates_status_check
  check (status in ('uploading', 'published', 'withdrawn', 'deleted'));

alter table public.shared_templates
  alter column source_reel_id drop not null;
alter table public.shared_templates
  drop constraint if exists shared_templates_source_reel_id_fkey;
alter table public.shared_templates
  add constraint shared_templates_source_reel_id_fkey
  foreign key (source_reel_id)
  references public.creator_reels(id)
  on delete set null;

-- Owners may remove the assets of a withdrawn listing only while its template
-- record still exists.  The delete RPC returns those paths only when nobody
-- has unlocked that listing.
create or replace function public.shared_template_asset_is_uploadable(p_name text)
returns boolean
language sql
stable
security definer
set search_path = public, storage
as $$
  select auth.uid() is not null and exists (
    select 1 from public.shared_templates t
    where t.owner_id = auth.uid()
      and t.status in ('uploading', 'withdrawn')
      and (t.preview_path = p_name or t.style_reference_path = p_name)
  );
$$;

create or replace function public.get_my_creator_reel_delete_paths(
  p_reel_id uuid
)
returns table (
  creator_object_paths text[],
  template_object_paths text[]
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_reel public.creator_reels%rowtype;
  v_template_paths text[] := '{}'::text[];
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  select * into v_reel
  from public.creator_reels
  where id = p_reel_id
    and owner_id = v_user
    and status in ('active', 'collected')
  for update;
  if not found then
    raise exception 'creator_reel_delete_not_available' using errcode = 'P0002';
  end if;

  perform 1
  from public.shared_templates t
  where t.owner_id = v_user and t.source_reel_id = p_reel_id
  for update;

  if exists (
    select 1 from public.shared_templates t
    where t.owner_id = v_user
      and t.source_reel_id = p_reel_id
      and t.status in ('uploading', 'published')
  ) then
    raise exception 'creator_reel_has_published_template' using errcode = '23503';
  end if;

  select coalesce(array_agg(asset_path), '{}'::text[])
  into v_template_paths
  from (
    select t.preview_path as asset_path
    from public.shared_templates t
    where t.owner_id = v_user
      and t.source_reel_id = p_reel_id
      and t.status = 'withdrawn'
      and not exists (
        select 1 from public.shared_template_redemptions r
        where r.template_id = t.id
      )
    union all
    select t.style_reference_path as asset_path
    from public.shared_templates t
    where t.owner_id = v_user
      and t.source_reel_id = p_reel_id
      and t.status = 'withdrawn'
      and t.style_reference_path is not null
      and not exists (
        select 1 from public.shared_template_redemptions r
        where r.template_id = t.id
      )
  ) template_assets;

  return query select
    array_remove(array[v_reel.grid_path, v_reel.style_reference_path], null),
    v_template_paths;
end;
$$;

create or replace function public.finalize_my_creator_reel_delete(
  p_reel_id uuid
)
returns boolean
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

  if not exists (
    select 1 from public.creator_reels r
    where r.id = p_reel_id
      and r.owner_id = v_user
      and r.status in ('active', 'collected')
  ) then
    raise exception 'creator_reel_delete_not_available' using errcode = 'P0002';
  end if;

  perform 1
  from public.shared_templates t
  where t.owner_id = v_user and t.source_reel_id = p_reel_id
  for update;

  if exists (
    select 1 from public.shared_templates t
    where t.owner_id = v_user
      and t.source_reel_id = p_reel_id
      and t.status in ('uploading', 'published')
  ) then
    raise exception 'creator_reel_has_published_template' using errcode = '23503';
  end if;

  -- No one unlocked these withdrawn listings, so erase their management row.
  delete from public.shared_templates t
  where t.owner_id = v_user
    and t.source_reel_id = p_reel_id
    and t.status = 'withdrawn'
    and not exists (
      select 1 from public.shared_template_redemptions r
      where r.template_id = t.id
    );

  -- Retain only a hidden, detached record when a member already unlocked it.
  -- Its immutable redemption snapshot remains usable for that member.
  update public.shared_templates t
  set status = 'deleted', source_reel_id = null
  where t.owner_id = v_user
    and t.source_reel_id = p_reel_id
    and t.status = 'withdrawn';

  delete from public.creator_reels
  where id = p_reel_id
    and owner_id = v_user
    and status in ('active', 'collected');
  if not found then
    raise exception 'creator_reel_delete_not_available' using errcode = 'P0002';
  end if;
  return true;
end;
$$;

create or replace function public.get_my_shared_template_sources()
returns table (source_reel_id uuid, status text)
language sql
stable
security definer
set search_path = public
as $$
  select t.source_reel_id, t.status
  from public.shared_templates t
  where t.owner_id = auth.uid()
    and t.status in ('uploading', 'published', 'withdrawn');
$$;

create or replace function public.list_my_shared_template_management(
  p_limit integer default 4,
  p_offset integer default 0
)
returns table (
  template_id uuid,
  source_reel_id uuid,
  title text,
  description text,
  apply_cost integer,
  status text,
  preview_path text,
  published_at timestamptz,
  updated_at timestamptz,
  unlock_count bigint,
  reward_credits bigint,
  total_count bigint
)
language sql
stable
security definer
set search_path = public
as $$
  with managed_templates as (
    select
      t.id as template_id,
      t.source_reel_id,
      t.title,
      t.description,
      t.apply_cost,
      t.status,
      t.preview_path,
      t.published_at,
      t.updated_at,
      count(r.id) filter (where r.consumer_id <> t.owner_id) as unlock_count,
      coalesce(sum(r.charged_cost) filter (where r.consumer_id <> t.owner_id), 0) as reward_credits
    from public.shared_templates t
    left join public.shared_template_redemptions r on r.template_id = t.id
    where t.owner_id = auth.uid()
      and t.status in ('published', 'withdrawn')
    group by t.id
  )
  select managed_templates.*, count(*) over () as total_count
  from managed_templates
  order by case when status = 'published' then 0 else 1 end, updated_at desc
  limit greatest(1, least(coalesce(p_limit, 4), 12))
  offset greatest(0, coalesce(p_offset, 0));
$$;

revoke all on function public.get_my_creator_reel_delete_paths(uuid) from public, anon;
revoke all on function public.finalize_my_creator_reel_delete(uuid) from public, anon;
revoke all on function public.get_my_shared_template_sources() from public, anon;
revoke all on function public.list_my_shared_template_management(integer, integer) from public, anon;
grant execute on function public.get_my_creator_reel_delete_paths(uuid) to authenticated;
grant execute on function public.finalize_my_creator_reel_delete(uuid) to authenticated;
grant execute on function public.get_my_shared_template_sources() to authenticated;
grant execute on function public.list_my_shared_template_management(integer, integer) to authenticated;

notify pgrst, 'reload schema';
