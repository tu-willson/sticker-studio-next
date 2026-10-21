-- Sticker Studio Next: private creator-reel snapshots.
--
-- One reel is one completed 4×2 generation (eight stickers).  Reels are
-- deliberately separate from library_projects: a project may keep many
-- generations, while a shareable template must later point to one immutable
-- reel.  This first migration creates only private subscriber storage; it
-- does not make anything public or alter existing member projects.

create table public.creator_reels (
  id uuid primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  source_project_id uuid not null,
  source_project_name text not null default ''
    check (char_length(source_project_name) <= 80),
  reel_number integer not null check (reel_number >= 1),
  status text not null default 'uploading'
    check (status in ('uploading', 'active', 'collected', 'published', 'expired')),
  snapshot jsonb not null default '{}'::jsonb
    check (jsonb_typeof(snapshot) = 'object'),
  grid_path text not null,
  style_reference_path text,
  grid_bytes bigint not null default 0 check (grid_bytes between 0 and 15000000),
  style_reference_bytes bigint not null default 0
    check (style_reference_bytes between 0 and 10000000),
  auto_expires_at timestamptz,
  created_at timestamptz not null default now(),
  completed_at timestamptz,
  updated_at timestamptz not null default now(),
  check (
    (status = 'uploading' and completed_at is null)
    or (status <> 'uploading' and completed_at is not null)
  )
);

create unique index creator_reels_owner_project_number_unique
  on public.creator_reels (owner_id, source_project_id, reel_number);
create index creator_reels_owner_auto_created_idx
  on public.creator_reels (owner_id, created_at asc)
  where status = 'active' and auto_expires_at is not null;
create index creator_reels_owner_project_created_idx
  on public.creator_reels (owner_id, source_project_id, created_at desc);

create trigger creator_reels_set_updated_at
before update on public.creator_reels
for each row execute function public.set_updated_at();

alter table public.creator_reels enable row level security;
revoke all on table public.creator_reels from anon, authenticated;
grant select on table public.creator_reels to authenticated;

create policy "creator_reels_select_own"
on public.creator_reels for select to authenticated
using (owner_id = (select auth.uid()));

-- The automatic reel needs only one compact 4×2 WebP plus an optional style
-- reference.  Full per-sticker PNG assets are created only when a member
-- later saves, collects, or publishes the reel.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'creator-reels', 'creator-reels', false, 25000000,
  array['image/webp']
)
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

create or replace function public.creator_reel_object_is_owned(p_name text)
returns boolean
language sql
stable
security definer
set search_path = public, storage
as $$
  select auth.uid() is not null and exists (
    select 1
    from public.creator_reels r
    where r.owner_id = auth.uid()
      and (p_name = r.grid_path or p_name = r.style_reference_path)
  );
$$;

drop policy if exists "creator_reels_storage_select_own" on storage.objects;
create policy "creator_reels_storage_select_own"
on storage.objects for select to authenticated
using (
  bucket_id = 'creator-reels'
  and public.creator_reel_object_is_owned(name)
);

drop policy if exists "creator_reels_storage_insert_own" on storage.objects;
create policy "creator_reels_storage_insert_own"
on storage.objects for insert to authenticated
with check (
  bucket_id = 'creator-reels'
  and public.creator_reel_object_is_owned(name)
);

drop policy if exists "creator_reels_storage_delete_own" on storage.objects;
create policy "creator_reels_storage_delete_own"
on storage.objects for delete to authenticated
using (
  bucket_id = 'creator-reels'
  and public.creator_reel_object_is_owned(name)
);

create or replace function public.get_my_creator_reel_status()
returns table (
  eligible boolean,
  product_code text,
  auto_reel_limit integer,
  retention_days integer,
  collected_reel_limit integer,
  publish_template_limit integer,
  active_auto_reel_count integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_entitlement record;
  v_auto_limit integer := 0;
  v_retention integer := 0;
  v_collected_limit integer := 0;
  v_publish_limit integer := 0;
  v_count integer := 0;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  select * into v_entitlement from public.get_my_effective_entitlement();
  if found and v_entitlement.entitlement_kind = 'subscription' then
    case v_entitlement.product_code
      when 'basic_monthly' then
        v_auto_limit := 20; v_retention := 30;
        v_collected_limit := 20; v_publish_limit := 10;
      when 'standard_monthly' then
        v_auto_limit := 30; v_retention := 45;
        v_collected_limit := 30; v_publish_limit := 15;
      when 'advanced_monthly' then
        v_auto_limit := 40; v_retention := 60;
        v_collected_limit := 40; v_publish_limit := 20;
    end case;
  end if;

  if v_auto_limit > 0 then
    select count(*)::integer into v_count
    from public.creator_reels r
    where r.owner_id = v_user
      and r.status = 'active'
      and r.auto_expires_at is not null
      and r.auto_expires_at > now();
  end if;

  return query select
    v_auto_limit > 0,
    case when v_auto_limit > 0 then v_entitlement.product_code else null end,
    v_auto_limit, v_retention, v_collected_limit, v_publish_limit, v_count;
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

  -- Any previously expired rows are retried for client-side Storage cleanup.
  update public.creator_reels
  set status = 'expired', completed_at = coalesce(completed_at, now())
  where owner_id = v_user
    and status = 'active'
    and auto_expires_at is not null
    and auto_expires_at <= now();

  select count(*)::integer into v_active_count
  from public.creator_reels
  where owner_id = v_user
    and status = 'active'
    and auto_expires_at is not null
    and auto_expires_at > now();

  -- A full rolling shelf replaces the oldest automatic reel.  Collected and
  -- published reels never participate in this query.
  if v_active_count >= v_auto_limit then
    with oldest as (
      select id
      from public.creator_reels
      where owner_id = v_user
        and status = 'active'
        and auto_expires_at is not null
      order by created_at asc
      limit v_active_count - v_auto_limit + 1
      for update
    )
    update public.creator_reels r
    set status = 'expired'
    from oldest
    where r.id = oldest.id;
  end if;

  select coalesce(max(reel_number), 0) + 1 into v_reel_number
  from public.creator_reels
  where owner_id = v_user and source_project_id = p_source_project_id;

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
  from public.creator_reels
  where owner_id = v_user
    and status = 'active'
    and auto_expires_at is not null
    and auto_expires_at > now();

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

create or replace function public.complete_creator_reel_upload(
  p_reel_id uuid,
  p_grid_bytes bigint,
  p_style_reference_bytes bigint default 0
)
returns boolean
language plpgsql
security definer
set search_path = public, storage
as $$
declare
  v_user uuid := auth.uid();
  v_reel public.creator_reels%rowtype;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_grid_bytes not between 1 and 15000000
     or p_style_reference_bytes not between 0 and 10000000 then
    raise exception 'invalid_creator_reel_asset_size' using errcode = '22023';
  end if;

  select * into v_reel from public.creator_reels
  where id = p_reel_id and owner_id = v_user and status = 'uploading'
  for update;
  if not found then
    raise exception 'creator_reel_upload_slot_not_found' using errcode = 'P0002';
  end if;
  if not exists (
       select 1 from storage.objects
       where bucket_id = 'creator-reels' and name = v_reel.grid_path
     )
     or (p_style_reference_bytes > 0 and not exists (
       select 1 from storage.objects
       where bucket_id = 'creator-reels' and name = v_reel.style_reference_path
     )) then
    raise exception 'creator_reel_assets_missing' using errcode = 'P0002';
  end if;

  update public.creator_reels
  set status = 'active', grid_bytes = p_grid_bytes,
      style_reference_bytes = p_style_reference_bytes,
      completed_at = now()
  where id = p_reel_id;
  return true;
end;
$$;

create or replace function public.cancel_creator_reel_upload(p_reel_id uuid)
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
  update public.creator_reels
  set status = 'expired', completed_at = now()
  where id = p_reel_id and owner_id = v_user and status = 'uploading';
  return found;
end;
$$;

create or replace function public.finalize_creator_reel_cleanup(p_reel_ids uuid[])
returns integer
language plpgsql
security definer
set search_path = public, storage
as $$
declare
  v_user uuid := auth.uid();
  v_deleted integer := 0;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if coalesce(array_length(p_reel_ids, 1), 0) = 0 then
    return 0;
  end if;

  delete from public.creator_reels r
  where r.owner_id = v_user
    and r.status = 'expired'
    and r.id = any(p_reel_ids)
    and not exists (
      select 1 from storage.objects o
      where o.bucket_id = 'creator-reels'
        and (o.name = r.grid_path or o.name = r.style_reference_path)
    );
  get diagnostics v_deleted = row_count;
  return v_deleted;
end;
$$;

revoke all on function public.creator_reel_object_is_owned(text) from public, anon;
revoke all on function public.get_my_creator_reel_status() from public, anon;
revoke all on function public.reserve_creator_reel(uuid, uuid, text, jsonb, boolean) from public, anon;
revoke all on function public.complete_creator_reel_upload(uuid, bigint, bigint) from public, anon;
revoke all on function public.cancel_creator_reel_upload(uuid) from public, anon;
revoke all on function public.finalize_creator_reel_cleanup(uuid[]) from public, anon;
grant execute on function public.creator_reel_object_is_owned(text) to authenticated;
grant execute on function public.get_my_creator_reel_status() to authenticated;
grant execute on function public.reserve_creator_reel(uuid, uuid, text, jsonb, boolean) to authenticated;
grant execute on function public.complete_creator_reel_upload(uuid, bigint, bigint) to authenticated;
grant execute on function public.cancel_creator_reel_upload(uuid) to authenticated;
grant execute on function public.finalize_creator_reel_cleanup(uuid[]) to authenticated;
