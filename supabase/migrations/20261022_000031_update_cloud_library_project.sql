-- Sticker Studio Next: allow a member to continue editing a cloud-loaded
-- project and explicitly save it back to the same library slot.
--
-- New assets reuse the member's existing private project path.  The final
-- metadata update is authorized server-side, so a client can never update
-- another member's project or bypass the normal storage policy.

drop policy if exists "member_projects_update_own" on storage.objects;
create policy "member_projects_update_own"
on storage.objects for update to authenticated
using (
  bucket_id = 'member-projects'
  and owner_id = (select auth.uid()::text)
  and public.member_project_path_is_allowed(name)
)
with check (
  bucket_id = 'member-projects'
  and owner_id = (select auth.uid()::text)
  and public.member_project_path_is_allowed(name)
);

create or replace function public.update_library_project(
  p_project_id uuid,
  p_title text,
  p_metadata jsonb,
  p_preview_bytes bigint,
  p_sticker_bytes bigint,
  p_source_photo_bytes bigint,
  p_style_reference_bytes bigint
)
returns table (project_id uuid, saved_count integer, library_capacity integer)
language plpgsql
security definer
set search_path = public, storage
as $$
declare
  v_user uuid := auth.uid();
  v_project public.library_projects%rowtype;
  v_title text := trim(coalesce(p_title, ''));
  v_keep_paths text[] := array[]::text[];
  v_capacity integer;
  v_count integer;
  v_sticker jsonb;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_project_id is null
     or char_length(v_title) not between 1 and 80
     or jsonb_typeof(coalesce(p_metadata, '{}'::jsonb)) <> 'object'
     or p_preview_bytes not between 1 and 3000000
     or p_sticker_bytes not between 1 and 30000000
     or p_source_photo_bytes not between 0 and 10000000
     or p_style_reference_bytes not between 0 and 10000000 then
    raise exception 'invalid_library_project_update' using errcode = '22023';
  end if;

  select * into v_project
  from public.library_projects
  where id = p_project_id and owner_id = v_user and status = 'active'
  for update;
  if not found then
    raise exception 'library_project_not_found' using errcode = 'P0002';
  end if;

  if not exists (
       select 1 from storage.objects
       where bucket_id = 'member-projects' and name = v_project.preview_path
     ) then
    raise exception 'library_assets_missing' using errcode = 'P0002';
  end if;

  v_keep_paths := array_append(v_keep_paths, v_project.preview_path);
  for v_sticker in
    select value
    from jsonb_array_elements(
      coalesce(p_metadata #> '{cloud_asset_manifest,stickers}', '[]'::jsonb)
    )
  loop
    if jsonb_typeof(v_sticker) = 'object'
       and coalesce(v_sticker->>'path', '') ~ '^stickers/[0-9]{3}\.png$' then
      v_keep_paths := array_append(
        v_keep_paths, v_project.sticker_prefix || (v_sticker->>'path')
      );
    end if;
  end loop;
  if cardinality(v_keep_paths) <= 1 then
    raise exception 'library_assets_missing' using errcode = 'P0002';
  end if;
  if p_source_photo_bytes > 0 then
    v_keep_paths := array_append(v_keep_paths, v_project.source_photo_path);
  end if;
  if p_style_reference_bytes > 0 then
    v_keep_paths := array_append(v_keep_paths, v_project.style_reference_path);
  end if;
  if exists (
       select 1 from unnest(v_keep_paths) as expected_path
       where not exists (
         select 1 from storage.objects
         where bucket_id = 'member-projects' and name = expected_path
       )
     ) then
    raise exception 'library_assets_missing' using errcode = 'P0002';
  end if;

  -- Remove assets no longer named in the newly saved manifest, including a
  -- removed private source/reference image or surplus sticker files.
  delete from storage.objects
  where bucket_id = 'member-projects'
    and name like v_user::text || '/' || p_project_id::text || '/%'
    and not (name = any(v_keep_paths));

  update public.library_projects
  set title = v_title,
      metadata = p_metadata,
      preview_bytes = p_preview_bytes,
      sticker_bytes = p_sticker_bytes,
      source_photo_bytes = p_source_photo_bytes,
      style_reference_bytes = p_style_reference_bytes,
      completed_at = now()
  where id = p_project_id;

  select library_capacity into v_capacity
  from public.get_my_effective_entitlement();
  select count(*)::integer into v_count
  from public.library_projects
  where owner_id = v_user and status in ('uploading', 'active');

  return query select p_project_id, v_count, coalesce(v_capacity, 5);
end;
$$;

revoke all on function public.update_library_project(
  uuid, text, jsonb, bigint, bigint, bigint, bigint
) from public, anon;
grant execute on function public.update_library_project(
  uuid, text, jsonb, bigint, bigint, bigint, bigint
) to authenticated;

notify pgrst, 'reload schema';
