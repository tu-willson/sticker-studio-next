-- Sticker Studio Next: private member cloud library.
--
-- A library slot is a capacity entitlement, not a generation credit.  The
-- Beta starts every member at five saved projects; a future subscription
-- webhook can change profiles.library_capacity without changing this schema.
-- Each project is represented by one private Storage folder containing only:
-- grid.webp, package.zip, and preview.webp.  We deliberately do not persist
-- duplicate copies of the eight stickers, main.png, and tab.png.

alter table public.profiles
  add column if not exists library_capacity integer not null default 5
    check (library_capacity between 1 and 1000);

create table public.library_projects (
  id uuid primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  title text not null check (char_length(trim(title)) between 1 and 80),
  metadata jsonb not null default '{}'::jsonb,
  status text not null default 'uploading'
    check (status in ('uploading', 'active')),
  grid_path text not null,
  package_path text not null,
  preview_path text not null,
  grid_bytes bigint not null default 0 check (grid_bytes between 0 and 15000000),
  package_bytes bigint not null default 0 check (package_bytes between 0 and 15000000),
  preview_bytes bigint not null default 0 check (preview_bytes between 0 and 3000000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  completed_at timestamptz
);

create index library_projects_owner_created_idx
  on public.library_projects (owner_id, created_at desc);

create trigger library_projects_set_updated_at
before update on public.library_projects
for each row execute function public.set_updated_at();

alter table public.library_projects enable row level security;
revoke all on table public.library_projects from anon, authenticated;
grant select on table public.library_projects to authenticated;

create policy "library_projects_select_own"
on public.library_projects for select to authenticated
using (owner_id = (select auth.uid()));

-- A private bucket is intentional: a URL alone never grants access to a
-- member's creations.  The three fixed names make it possible for the Storage
-- policies below to reject arbitrary uploads.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'member-projects', 'member-projects', false, 15000000,
  array['image/webp', 'application/zip']
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
      and (p_name = p.grid_path or p_name = p.package_path or p_name = p.preview_path)
  );
$$;

drop policy if exists "member_projects_select_own" on storage.objects;
drop policy if exists "member_projects_insert_slot_only" on storage.objects;
drop policy if exists "member_projects_delete_own" on storage.objects;

create policy "member_projects_select_own"
on storage.objects for select to authenticated
using (
  bucket_id = 'member-projects'
  and public.member_project_path_is_allowed(name)
);

create policy "member_projects_insert_slot_only"
on storage.objects for insert to authenticated
with check (
  bucket_id = 'member-projects'
  and public.member_project_path_is_allowed(name)
  and owner_id = (select auth.uid()::text)
);

create policy "member_projects_delete_own"
on storage.objects for delete to authenticated
using (
  bucket_id = 'member-projects'
  and owner_id = (select auth.uid()::text)
  and public.member_project_path_is_allowed(name)
);

create or replace function public.get_my_library_status()
returns table (saved_count integer, library_capacity integer)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_capacity integer;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  select p.library_capacity into v_capacity
  from public.profiles p where p.id = v_user;

  return query
  select count(*)::integer, coalesce(v_capacity, 5)
  from public.library_projects lp
  where lp.owner_id = v_user and lp.status in ('uploading', 'active');
end;
$$;

create or replace function public.create_library_project_slot(
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
  -- Failed uploads never become active.  Reclaim their capacity after 30 min.
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
    id, owner_id, title, metadata, grid_path, package_path, preview_path
  ) values (
    p_project_id, v_user, v_title, coalesce(p_metadata, '{}'::jsonb),
    v_prefix || 'grid.webp', v_prefix || 'package.zip', v_prefix || 'preview.webp'
  );

  return query select p_project_id, v_count + 1, v_capacity;
end;
$$;

create or replace function public.complete_library_project_upload(
  p_project_id uuid,
  p_grid_bytes bigint,
  p_package_bytes bigint,
  p_preview_bytes bigint
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
  if p_grid_bytes not between 1 and 15000000
     or p_package_bytes not between 1 and 15000000
     or p_preview_bytes not between 1 and 3000000 then
    raise exception 'invalid_library_asset_size' using errcode = '22023';
  end if;

  select * into v_project from public.library_projects
  where id = p_project_id and owner_id = v_user and status = 'uploading'
  for update;
  if not found then
    raise exception 'library_upload_slot_not_found' using errcode = 'P0002';
  end if;

  if not exists (select 1 from storage.objects where bucket_id = 'member-projects' and name = v_project.grid_path)
     or not exists (select 1 from storage.objects where bucket_id = 'member-projects' and name = v_project.package_path)
     or not exists (select 1 from storage.objects where bucket_id = 'member-projects' and name = v_project.preview_path) then
    raise exception 'library_assets_missing' using errcode = 'P0002';
  end if;

  update public.library_projects
  set status = 'active', grid_bytes = p_grid_bytes, package_bytes = p_package_bytes,
      preview_bytes = p_preview_bytes, completed_at = now()
  where id = p_project_id;
  return true;
end;
$$;

create or replace function public.get_my_library_project_delete_paths(p_project_id uuid)
returns table (grid_path text, package_path text, preview_path text)
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
  select p.grid_path, p.package_path, p.preview_path
  from public.library_projects p
  where p.id = p_project_id and p.owner_id = v_user;
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
  v_project public.library_projects%rowtype;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  select * into v_project from public.library_projects
  where id = p_project_id and owner_id = v_user
  for update;
  if not found then
    return false;
  end if;
  if exists (
    select 1 from storage.objects
    where bucket_id = 'member-projects'
      and name in (v_project.grid_path, v_project.package_path, v_project.preview_path)
  ) then
    raise exception 'library_assets_not_deleted' using errcode = 'P0002';
  end if;
  delete from public.library_projects where id = p_project_id;
  return true;
end;
$$;

revoke all on function public.member_project_path_is_allowed(text) from public, anon;
revoke all on function public.get_my_library_status() from public, anon;
revoke all on function public.create_library_project_slot(uuid, text, jsonb) from public, anon;
revoke all on function public.complete_library_project_upload(uuid, bigint, bigint, bigint) from public, anon;
revoke all on function public.get_my_library_project_delete_paths(uuid) from public, anon;
revoke all on function public.finalize_my_library_project_delete(uuid) from public, anon;
grant execute on function public.member_project_path_is_allowed(text) to authenticated;
grant execute on function public.get_my_library_status() to authenticated;
grant execute on function public.create_library_project_slot(uuid, text, jsonb) to authenticated;
grant execute on function public.complete_library_project_upload(uuid, bigint, bigint, bigint) to authenticated;
grant execute on function public.get_my_library_project_delete_paths(uuid) to authenticated;
grant execute on function public.finalize_my_library_project_delete(uuid) to authenticated;
