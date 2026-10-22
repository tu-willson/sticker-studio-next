-- Keep marketplace templates immutable while allowing a member to rename the
-- private shelf label of any collected source reel.

create or replace function public.rename_my_collected_creator_reel(
  p_reel_id uuid,
  p_title text
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_title text := trim(coalesce(p_title, ''));
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_reel_id is null or char_length(v_title) not between 1 and 80 then
    raise exception 'invalid_creator_reel_title' using errcode = '22023';
  end if;

  update public.creator_reels r
  set
    source_project_name = v_title,
    snapshot = jsonb_set(
      coalesce(r.snapshot, '{}'::jsonb),
      '{project_name}',
      to_jsonb(v_title),
      true
    )
  where r.id = p_reel_id
    and r.owner_id = v_user
    and r.status = 'collected';

  if not found then
    raise exception 'creator_reel_rename_not_available' using errcode = 'P0002';
  end if;
  return true;
end;
$$;

revoke all on function public.rename_my_collected_creator_reel(uuid, text) from public, anon;
grant execute on function public.rename_my_collected_creator_reel(uuid, text) to authenticated;

notify pgrst, 'reload schema';
