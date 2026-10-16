-- Sticker Studio Next: store only a member's selected sticker assets.
--
-- New projects no longer duplicate the same data as a 4×2 source grid and a
-- downloadable ZIP.  They store the selected PNGs, a compact preview, and
-- optionally private source materials so a member can continue editing later.

alter table public.library_projects
  add column if not exists sticker_prefix text,
  add column if not exists source_photo_path text,
  add column if not exists style_reference_path text,
  add column if not exists sticker_bytes bigint not null default 0
    check (sticker_bytes between 0 and 30000000),
  add column if not exists source_photo_bytes bigint not null default 0
    check (source_photo_bytes between 0 and 10000000),
  add column if not exists style_reference_bytes bigint not null default 0
    check (style_reference_bytes between 0 and 10000000);

-- Keep legacy rows readable, but new rows do not require a grid or ZIP.
alter table public.library_projects alter column grid_path drop not null;
alter table public.library_projects alter column package_path drop not null;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'member-projects', 'member-projects', false, 30000000,
  array['image/webp', 'image/png', 'application/zip']
)
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

create or replace function public.member_project_path_is_allowed(p_name text)
returns boolean
language sql
stable
security definer
set search_path = public, storage
as $$
  select auth.uid() is not null and exists (
    select 1
    from public.library_projects p
    where p.owner_id = auth.uid()
      and p.status in ('uploading', 'active')
      and p.id::text = (storage.foldername(p_name))[2]
      and (
        p_name = p.preview_path
        or p_name = p.grid_path
        or p_name = p.package_path
        or p_name = p.source_photo_path
        or p_name = p.style_reference_path
        or (
          coalesce(p.sticker_prefix, '') <> ''
          and p_name like p.sticker_prefix || '%'
        )
      )
  );
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
  where owner_id = v_user
    and status = 'uploading'
    and created_at < now() - interval '30 minutes';

  select p.library_capacity into v_capacity
  from public.profiles p where p.id = v_user;
  v_capacity := coalesce(v_capacity, 5);

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

drop function if exists public.complete_library_project_upload(uuid, bigint, bigint, bigint);
create function public.complete_library_project_upload(
  p_project_id uuid,
  p_preview_bytes bigint,
  p_sticker_bytes bigint,
  p_source_photo_bytes bigint,
  p_style_reference_bytes bigint
)
returns boolean
language plpgsql
security definer
set search_path = public, storage
as $$
declare
  v_user uuid := auth.uid();
  v_project public.library_projects%rowtype;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_preview_bytes not between 1 and 3000000
     or p_sticker_bytes not between 1 and 30000000
     or p_source_photo_bytes not between 0 and 10000000
     or p_style_reference_bytes not between 0 and 10000000 then
    raise exception 'invalid_library_asset_size' using errcode = '22023';
  end if;

  select * into v_project from public.library_projects
  where id = p_project_id and owner_id = v_user and status = 'uploading'
  for update;
  if not found then
    raise exception 'library_upload_slot_not_found' using errcode = 'P0002';
  end if;
  if not exists (
       select 1 from storage.objects
       where bucket_id = 'member-projects' and name = v_project.preview_path
     )
     or not exists (
       select 1 from storage.objects
       where bucket_id = 'member-projects'
         and name like v_project.sticker_prefix || '%'
     )
     or (p_source_photo_bytes > 0 and not exists (
       select 1 from storage.objects
       where bucket_id = 'member-projects' and name = v_project.source_photo_path
     ))
     or (p_style_reference_bytes > 0 and not exists (
       select 1 from storage.objects
       where bucket_id = 'member-projects' and name = v_project.style_reference_path
     )) then
    raise exception 'library_assets_missing' using errcode = 'P0002';
  end if;

  update public.library_projects
  set status = 'active',
      grid_bytes = 0,
      package_bytes = 0,
      preview_bytes = p_preview_bytes,
      sticker_bytes = p_sticker_bytes,
      source_photo_bytes = p_source_photo_bytes,
      style_reference_bytes = p_style_reference_bytes,
      completed_at = now()
  where id = p_project_id;
  return true;
end;
$$;

drop function if exists public.get_my_library_project_delete_paths(uuid);
create function public.get_my_library_project_delete_paths(p_project_id uuid)
returns table (object_paths text[])
language plpgsql
security definer
set search_path = public, storage
as $$
declare
  v_user uuid := auth.uid();
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  return query
  select coalesce(array_agg(o.name order by o.name), array[]::text[])
  from storage.objects o
  where o.bucket_id = 'member-projects'
    and o.name like v_user::text || '/' || p_project_id::text || '/%'
    and exists (
      select 1 from public.library_projects p
      where p.id = p_project_id and p.owner_id = v_user
    );
end;
$$;

create or replace function public.finalize_my_library_project_delete(p_project_id uuid)
returns boolean
language plpgsql
security definer
set search_path = public, storage
as $$
declare
  v_user uuid := auth.uid();
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if exists (
    select 1 from storage.objects
    where bucket_id = 'member-projects'
      and name like v_user::text || '/' || p_project_id::text || '/%'
  ) then
    raise exception 'library_assets_not_deleted' using errcode = 'P0002';
  end if;
  delete from public.library_projects
  where id = p_project_id and owner_id = v_user;
  return found;
end;
$$;

revoke all on function public.member_project_path_is_allowed(text) from public, anon;
revoke all on function public.create_library_project_slot(uuid, text, jsonb) from public, anon;
revoke all on function public.complete_library_project_upload(uuid, bigint, bigint, bigint, bigint) from public, anon;
revoke all on function public.get_my_library_project_delete_paths(uuid) from public, anon;
revoke all on function public.finalize_my_library_project_delete(uuid) from public, anon;
grant execute on function public.member_project_path_is_allowed(text) to authenticated;
grant execute on function public.create_library_project_slot(uuid, text, jsonb) to authenticated;
grant execute on function public.complete_library_project_upload(uuid, bigint, bigint, bigint, bigint) to authenticated;
grant execute on function public.get_my_library_project_delete_paths(uuid) to authenticated;
grant execute on function public.finalize_my_library_project_delete(uuid) to authenticated;
