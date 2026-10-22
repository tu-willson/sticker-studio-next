-- A withdrawn template is intentionally still visible to members who have
-- unlocked it.  Favorites must accept that same visible set; otherwise the
-- card offers a button which always fails with invalid_shared_template.

create or replace function public.toggle_shared_template_favorite(p_template_id uuid)
returns table (is_favorited boolean)
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

  if not exists (
    select 1
    from public.shared_templates template
    where template.id = p_template_id
      and (
        template.status = 'published'
        or exists (
          select 1
          from public.shared_template_redemptions redemption
          where redemption.template_id = template.id
            and redemption.consumer_id = v_user
        )
      )
  ) then
    raise exception 'invalid_shared_template' using errcode = '22023';
  end if;

  delete from public.shared_template_favorites favorite
  where favorite.template_id = p_template_id and favorite.user_id = v_user;
  if found then
    return query select false;
    return;
  end if;

  insert into public.shared_template_favorites (template_id, user_id)
  values (p_template_id, v_user);
  return query select true;
end;
$$;

revoke all on function public.toggle_shared_template_favorite(uuid) from public, anon;
grant execute on function public.toggle_shared_template_favorite(uuid) to authenticated;

notify pgrst, 'reload schema';
