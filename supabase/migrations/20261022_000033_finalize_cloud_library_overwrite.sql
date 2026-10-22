-- Sticker Studio Next: make the final overwrite commit independent from
-- Storage cleanup.  The client uploads every selected asset before invoking
-- this function; a stale, unreferenced private file must never block the
-- member from saving an edited cloud project.

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
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_title text := trim(coalesce(p_title, ''));
  v_prefix text;
  v_capacity integer;
  v_count integer;
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

  v_prefix := v_user::text || '/' || p_project_id::text || '/';
  update public.library_projects
  set title = v_title,
      metadata = p_metadata,
      preview_path = v_prefix || 'preview.webp',
      sticker_prefix = v_prefix || 'stickers/',
      source_photo_path = v_prefix || 'source-photo.webp',
      style_reference_path = v_prefix || 'style-reference.webp',
      preview_bytes = p_preview_bytes,
      sticker_bytes = p_sticker_bytes,
      source_photo_bytes = p_source_photo_bytes,
      style_reference_bytes = p_style_reference_bytes,
      completed_at = now()
  where id = p_project_id
    and owner_id = v_user
    and status = 'active';

  if not found then
    raise exception 'library_project_not_found' using errcode = 'P0002';
  end if;

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
