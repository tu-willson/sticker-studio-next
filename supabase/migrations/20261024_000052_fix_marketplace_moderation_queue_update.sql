-- Fix the moderation action transaction.
--
-- The original function returned a column named template_id and later used an
-- unqualified `where template_id = ...` while updating the review queue.
-- PL/pgSQL sees both names and stops the transaction as ambiguous. Qualifying
-- the queue column keeps the whole moderation action atomic.

create or replace function public.admin_moderate_shared_template(
  p_template_id uuid,
  p_action text,
  p_note text
)
returns table (
  template_id uuid,
  template_status text,
  review_status text,
  owner_marketplace_suspended boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_actor uuid := auth.uid();
  v_template public.shared_templates%rowtype;
  v_note text := trim(coalesce(p_note, ''));
  v_action text := trim(coalesce(p_action, ''));
  v_previous_status text;
  v_review_status text;
  v_resolution text;
  v_suspended boolean := false;
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  if p_template_id is null
     or v_action not in (
       'dismiss_reports', 'withdraw_listing', 'restore_listing',
       'suspend_creator_marketplace', 'restore_creator_marketplace'
     )
     or char_length(v_note) not between 3 and 300 then
    raise exception 'invalid_marketplace_moderation_action' using errcode = '22023';
  end if;

  select * into v_template
  from public.shared_templates template
  where template.id = p_template_id
  for update;
  if not found then
    raise exception 'shared_template_not_found' using errcode = 'P0002';
  end if;
  if not exists (
    select 1 from public.shared_template_moderation_queue queue
    where queue.template_id = v_template.id
  ) then
    raise exception 'marketplace_review_not_queued' using errcode = 'P0002';
  end if;
  if v_action in ('suspend_creator_marketplace', 'restore_creator_marketplace')
     and exists (
       select 1 from public.admin_users admin
       where admin.user_id = v_template.owner_id
     ) then
    raise exception 'cannot_moderate_administrator' using errcode = '42501';
  end if;

  perform 1 from public.profiles profile
  where profile.id = v_template.owner_id
  for update;
  if not found then
    raise exception 'member_not_found' using errcode = 'P0002';
  end if;

  v_previous_status := v_template.status;
  if v_action = 'dismiss_reports' then
    v_review_status := 'dismissed';
    v_resolution := 'dismiss_reports';
  elsif v_action = 'withdraw_listing' then
    if v_template.status = 'published' then
      update public.shared_templates
      set status = 'withdrawn'
      where id = v_template.id;
      v_template.status := 'withdrawn';
    end if;
    v_review_status := 'reviewed';
    v_resolution := 'withdraw_listing';
  elsif v_action = 'restore_listing' then
    if v_template.status <> 'withdrawn' then
      raise exception 'shared_template_not_withdrawn' using errcode = '22023';
    end if;
    if exists (
      select 1 from public.profiles profile
      where profile.id = v_template.owner_id
        and profile.marketplace_suspended_at is not null
    ) then
      raise exception 'creator_marketplace_suspended' using errcode = '42501';
    end if;
    update public.shared_templates
    set status = 'published', published_at = coalesce(published_at, now())
    where id = v_template.id;
    v_template.status := 'published';
    v_review_status := 'dismissed';
    v_resolution := 'restore_listing';
  elsif v_action = 'suspend_creator_marketplace' then
    update public.profiles
    set marketplace_suspended_at = now(), marketplace_suspension_reason = v_note
    where id = v_template.owner_id;
    update public.shared_templates
    set status = 'withdrawn'
    where owner_id = v_template.owner_id and status = 'published';
    v_template.status := case when v_template.status = 'published' then 'withdrawn' else v_template.status end;
    v_suspended := true;
    v_review_status := 'reviewed';
    v_resolution := 'suspend_creator_marketplace';
  else
    update public.profiles
    set marketplace_suspended_at = null, marketplace_suspension_reason = null
    where id = v_template.owner_id;
    v_suspended := false;
    v_review_status := 'dismissed';
    v_resolution := 'restore_creator_marketplace';
  end if;

  update public.shared_template_moderation_queue as queue
  set review_status = v_review_status,
      reviewed_at = now(),
      reviewed_by_user_id = v_actor,
      review_note = v_note,
      resolution = v_resolution
  where queue.template_id = v_template.id;

  select profile.marketplace_suspended_at is not null into v_suspended
  from public.profiles profile
  where profile.id = v_template.owner_id;

  insert into public.marketplace_moderation_actions (
    template_id, owner_id, actor_user_id, action, note,
    previous_template_status, resulting_template_status, resulting_review_status
  ) values (
    v_template.id, v_template.owner_id, v_actor, v_resolution, v_note,
    v_previous_status, v_template.status, v_review_status
  );

  return query
  select v_template.id, v_template.status, v_review_status, coalesce(v_suspended, false);
end;
$$;

grant execute on function public.admin_moderate_shared_template(uuid, text, text) to authenticated;

notify pgrst, 'reload schema';
