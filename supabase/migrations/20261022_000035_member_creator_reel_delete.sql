-- Sticker Studio Next: let a member remove a private active/collected reel.
-- A reel that is the source of a sharing template remains immutable so prior
-- template redemptions never lose their auditable source.

create or replace function public.get_my_creator_reel_delete_paths(
  p_reel_id uuid
)
returns table (object_paths text[])
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_reel public.creator_reels%rowtype;
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
  if exists (
    select 1 from public.shared_templates
    where source_reel_id = p_reel_id
  ) then
    raise exception 'creator_reel_has_published_template' using errcode = '23503';
  end if;

  return query select array_remove(
    array[v_reel.grid_path, v_reel.style_reference_path], null
  );
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
  if exists (
    select 1 from public.shared_templates
    where source_reel_id = p_reel_id
  ) then
    raise exception 'creator_reel_has_published_template' using errcode = '23503';
  end if;

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

revoke all on function public.get_my_creator_reel_delete_paths(uuid) from public, anon;
revoke all on function public.finalize_my_creator_reel_delete(uuid) from public, anon;
grant execute on function public.get_my_creator_reel_delete_paths(uuid) to authenticated;
grant execute on function public.finalize_my_creator_reel_delete(uuid) to authenticated;

notify pgrst, 'reload schema';
