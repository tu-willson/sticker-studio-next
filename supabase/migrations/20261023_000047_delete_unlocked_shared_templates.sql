-- A member can permanently remove one private unlocked-template snapshot.
-- This only removes their own redemption and related private marketplace
-- metadata; it never alters the publisher's template, assets, or rewards.

create or replace function public.delete_my_unlocked_shared_template(
  p_template_id uuid
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_deleted_redemption_id uuid;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_template_id is null then
    raise exception 'unlocked_template_not_found' using errcode = '22023';
  end if;

  delete from public.shared_template_redemptions redemption
  using public.shared_templates template
  where redemption.template_id = template.id
    and redemption.template_id = p_template_id
    and redemption.consumer_id = v_user
    and template.owner_id <> v_user
  returning redemption.id into v_deleted_redemption_id;

  if v_deleted_redemption_id is null then
    raise exception 'unlocked_template_not_found' using errcode = '22023';
  end if;

  -- Clear only the member's own auxiliary records.  If they choose to buy the
  -- template again later, it starts as a clean, new personal unlock.
  delete from public.shared_template_favorites
  where template_id = p_template_id and user_id = v_user;

  delete from public.shared_template_reports
  where template_id = p_template_id and reporter_id = v_user;

  return true;
end;
$$;

revoke all on function public.delete_my_unlocked_shared_template(uuid) from public, anon;
grant execute on function public.delete_my_unlocked_shared_template(uuid) to authenticated;

notify pgrst, 'reload schema';
