-- Temporary test policy: one eligible report places a listing in the review
-- queue.  Restore the threshold to three after testing is complete.
alter table public.shared_template_moderation_queue
  drop constraint if exists shared_template_moderation_queue_report_count_check;
alter table public.shared_template_moderation_queue
  add constraint shared_template_moderation_queue_report_count_check
  check (report_count >= 1);

create or replace function public.report_shared_template(p_template_id uuid)
returns table (
  report_count integer,
  queued_for_review boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_owner uuid;
  v_count integer := 0;
  v_used integer := 0;
  v_today date := (now() at time zone 'Asia/Taipei')::date;
  v_entitlement record;
  v_allowed boolean := false;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  select * into v_entitlement from public.get_my_effective_entitlement();
  v_allowed := v_entitlement.entitlement_kind = 'subscription'
    or v_entitlement.product_code = 'credit_pack_7';
  if not v_allowed then
    raise exception 'marketplace_report_membership_required' using errcode = '42501';
  end if;

  select template.owner_id into v_owner
  from public.shared_templates as template
  where template.id = p_template_id
    and (
      template.status = 'published'
      or exists (
        select 1
        from public.shared_template_redemptions as redemption
        where redemption.template_id = template.id
          and redemption.consumer_id = v_user
      )
    );
  if v_owner is null then
    raise exception 'invalid_shared_template' using errcode = '22023';
  end if;
  if v_owner = v_user then
    raise exception 'cannot_report_own_template' using errcode = '42501';
  end if;

  if exists (
    select 1 from public.shared_template_reports as report
    where report.template_id = p_template_id and report.reporter_id = v_user
  ) then
    select count(*)::integer into v_count
    from public.shared_template_reports as report
    where report.template_id = p_template_id;
    return query select v_count, v_count >= 1;
    return;
  end if;

  select count(*)::integer into v_used
  from public.shared_template_reports as report
  where report.reporter_id = v_user
    and (report.created_at at time zone 'Asia/Taipei')::date = v_today;
  if v_used >= 2 then
    raise exception 'marketplace_report_daily_limit_reached' using errcode = '22023';
  end if;

  insert into public.shared_template_reports (template_id, reporter_id)
  values (p_template_id, v_user);

  select count(*)::integer into v_count
  from public.shared_template_reports as report
  where report.template_id = p_template_id;

  if v_count >= 1 then
    insert into public.shared_template_moderation_queue (
      template_id, report_count, review_status, first_reported_at, last_reported_at
    )
    values (p_template_id, v_count, 'pending_review', now(), now())
    on conflict (template_id) do update
    set report_count = excluded.report_count,
        review_status = case
          when public.shared_template_moderation_queue.review_status = 'dismissed'
            then 'pending_review'
          else public.shared_template_moderation_queue.review_status
        end,
        last_reported_at = now();
  end if;

  return query select v_count, v_count >= 1;
end;
$$;

-- Only the dedicated appeal RPC can temporarily bypass the republish guard.
create or replace function public.prevent_locked_shared_template_republication()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if public.is_beta_admin()
     and current_setting('app.shared_template_appeal_restore', true) = 'enabled' then
    return new;
  end if;

  if old.moderation_republish_blocked
     and not new.moderation_republish_blocked then
    raise exception 'shared_template_republication_blocked' using errcode = '42501';
  end if;
  if old.moderation_republish_blocked
     and new.status = 'published'
     and old.status is distinct from 'published' then
    raise exception 'shared_template_republication_blocked' using errcode = '42501';
  end if;
  return new;
end;
$$;

alter table public.marketplace_moderation_actions
  drop constraint if exists marketplace_moderation_actions_action_check;
alter table public.marketplace_moderation_actions
  add constraint marketplace_moderation_actions_action_check
  check (action in (
    'dismiss_reports', 'withdraw_listing', 'restore_listing',
    'appeal_restore_listing',
    'suspend_creator_marketplace', 'restore_creator_marketplace'
  ));

create or replace function public.admin_find_reported_listing_appeals(p_owner_email text)
returns table (
  template_id uuid,
  title text,
  preview_path text,
  owner_email text,
  owner_name text,
  report_count integer,
  reviewed_at timestamptz,
  review_note text,
  locked_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_email text := lower(trim(coalesce(p_owner_email, '')));
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  if v_email = '' then
    return;
  end if;

  return query
  select
    template.id,
    template.title,
    template.preview_path,
    coalesce(profile.email, '')::text,
    coalesce(profile.display_name, '')::text,
    queue.report_count,
    queue.reviewed_at,
    queue.review_note,
    template.moderation_republish_blocked_at
  from public.shared_templates as template
  join public.shared_template_moderation_queue as queue
    on queue.template_id = template.id
  left join public.profiles as profile on profile.id = template.owner_id
  where lower(coalesce(profile.email, '')) = v_email
    and template.moderation_republish_blocked
    and queue.review_status = 'reviewed'
    and queue.resolution = 'withdraw_listing'
  order by queue.reviewed_at desc nulls last, template.updated_at desc;
end;
$$;

create or replace function public.admin_restore_reported_listing_after_appeal(
  p_template_id uuid,
  p_note text
)
returns table (
  template_id uuid,
  template_status text,
  review_status text
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_actor uuid := auth.uid();
  v_template public.shared_templates%rowtype;
  v_note text := trim(coalesce(p_note, ''));
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  if p_template_id is null or char_length(v_note) not between 3 and 300 then
    raise exception 'invalid_marketplace_appeal_restore' using errcode = '22023';
  end if;

  select * into v_template
  from public.shared_templates as template
  where template.id = p_template_id
  for update;
  if not found or not v_template.moderation_republish_blocked then
    raise exception 'reported_listing_not_locked' using errcode = 'P0002';
  end if;
  if exists (
    select 1 from public.profiles as profile
    where profile.id = v_template.owner_id
      and profile.marketplace_suspended_at is not null
  ) then
    raise exception 'creator_marketplace_suspended' using errcode = '42501';
  end if;
  if not exists (
    select 1 from public.shared_template_moderation_queue as queue
    where queue.template_id = v_template.id
      and queue.review_status = 'reviewed'
      and queue.resolution = 'withdraw_listing'
  ) then
    raise exception 'reported_listing_not_locked' using errcode = 'P0002';
  end if;

  perform set_config('app.shared_template_appeal_restore', 'enabled', true);
  update public.shared_templates as template
  set
    status = 'published',
    published_at = coalesce(template.published_at, now()),
    moderation_republish_blocked = false,
    moderation_republish_blocked_at = null
  where template.id = v_template.id;

  update public.shared_template_moderation_queue as queue
  set
    review_status = 'dismissed',
    reviewed_at = now(),
    reviewed_by_user_id = v_actor,
    review_note = v_note,
    resolution = 'appeal_restore_listing'
  where queue.template_id = v_template.id;

  insert into public.marketplace_moderation_actions (
    template_id, owner_id, actor_user_id, action, note,
    previous_template_status, resulting_template_status, resulting_review_status
  ) values (
    v_template.id, v_template.owner_id, v_actor, 'appeal_restore_listing', v_note,
    v_template.status, 'published', 'dismissed'
  );

  return query select v_template.id, 'published'::text, 'dismissed'::text;
end;
$$;

revoke all on function public.admin_find_reported_listing_appeals(text) from public, anon;
revoke all on function public.admin_restore_reported_listing_after_appeal(uuid, text) from public, anon;
grant execute on function public.admin_find_reported_listing_appeals(text) to authenticated;
grant execute on function public.admin_restore_reported_listing_after_appeal(uuid, text) to authenticated;

notify pgrst, 'reload schema';
