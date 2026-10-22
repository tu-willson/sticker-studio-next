-- Sticker Studio Next: let a member name a private collected reel.  A reel
-- that already backs a share template remains immutable, so the published
-- template and its source snapshot can never drift apart.

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
  if exists (
    select 1 from public.shared_templates t
    where t.source_reel_id = p_reel_id
  ) then
    raise exception 'creator_reel_rename_not_available' using errcode = '23503';
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
