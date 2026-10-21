-- Fix the creator-reel reservation function.
-- PostgreSQL exposes RETURNS TABLE columns as PL/pgSQL variables.  Qualifying
-- creator_reels columns prevents auto_expires_at from becoming ambiguous.

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
  v_entitlement record;
  v_auto_limit integer := 0;
  v_retention integer := 0;
  v_active_count integer := 0;
  v_reel_number integer := 1;
  v_prefix text;
  v_grid_path text;
  v_style_path text;
  v_expires_at timestamptz;
  v_cleanup_ids uuid[] := array[]::uuid[];
  v_cleanup_paths text[] := array[]::text[];
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_reel_id is null or p_source_project_id is null
     or jsonb_typeof(coalesce(p_snapshot, '{}'::jsonb)) <> 'object' then
    raise exception 'invalid_creator_reel' using errcode = '22023';
  end if;

  select * into v_entitlement from public.get_my_effective_entitlement();
  if found and v_entitlement.entitlement_kind = 'subscription' then
    case v_entitlement.product_code
      when 'basic_monthly' then v_auto_limit := 20; v_retention := 30;
      when 'standard_monthly' then v_auto_limit := 30; v_retention := 45;
      when 'advanced_monthly' then v_auto_limit := 40; v_retention := 60;
    end case;
  end if;
  if v_auto_limit = 0 then
    raise exception 'creator_reels_subscription_required' using errcode = '42501';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 41));

  update public.creator_reels r
  set status = 'expired', completed_at = coalesce(r.completed_at, now())
  where r.owner_id = v_user
    and r.status = 'active'
    and r.auto_expires_at is not null
    and r.auto_expires_at <= now();

  select count(*)::integer into v_active_count
  from public.creator_reels r
  where r.owner_id = v_user
    and r.status = 'active'
    and r.auto_expires_at is not null
    and r.auto_expires_at > now();

  if v_active_count >= v_auto_limit then
    with oldest as (
      select r.id
      from public.creator_reels r
      where r.owner_id = v_user
        and r.status = 'active'
        and r.auto_expires_at is not null
      order by r.created_at asc
      limit v_active_count - v_auto_limit + 1
      for update
    )
    update public.creator_reels r
    set status = 'expired'
    from oldest
    where r.id = oldest.id;
  end if;

  select coalesce(max(r.reel_number), 0) + 1 into v_reel_number
  from public.creator_reels r
  where r.owner_id = v_user and r.source_project_id = p_source_project_id;

  v_prefix := v_user::text || '/' || p_reel_id::text || '/';
  v_grid_path := v_prefix || 'grid.webp';
  v_style_path := case when p_has_style_reference then v_prefix || 'style-reference.webp' else null end;
  v_expires_at := now() + make_interval(days => v_retention);

  insert into public.creator_reels (
    id, owner_id, source_project_id, source_project_name, reel_number,
    status, snapshot, grid_path, style_reference_path, auto_expires_at
  ) values (
    p_reel_id, v_user, p_source_project_id,
    left(trim(coalesce(p_source_project_name, '')), 80), v_reel_number,
    'uploading', p_snapshot, v_grid_path, v_style_path, v_expires_at
  );

  select count(*)::integer into v_active_count
  from public.creator_reels r
  where r.owner_id = v_user
    and r.status = 'active'
    and r.auto_expires_at is not null
    and r.auto_expires_at > now();

  select
    coalesce(array_agg(r.id order by r.created_at asc), array[]::uuid[]),
    coalesce(
      array_agg(r.grid_path order by r.created_at asc)
        || array_remove(array_agg(r.style_reference_path order by r.created_at asc), null),
      array[]::text[]
    )
  into v_cleanup_ids, v_cleanup_paths
  from public.creator_reels r
  where r.owner_id = v_user and r.status = 'expired';

  return query select
    p_reel_id, v_grid_path, v_style_path, v_auto_limit,
    v_active_count + 1, v_expires_at, v_cleanup_ids, v_cleanup_paths;
end;
$$;

notify pgrst, 'reload schema';
