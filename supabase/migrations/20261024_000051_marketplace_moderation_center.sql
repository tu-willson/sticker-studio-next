-- Marketplace moderation centre.
--
-- Three independent reports already place a listing in the queue.  This
-- migration adds the administrator-only review workflow, an immutable action
-- audit trail, and a reversible marketplace-only suspension.  A suspension
-- removes the creator's public listings but deliberately never deletes a
-- buyer's immutable redemption snapshot.

alter table public.profiles
  add column if not exists marketplace_suspended_at timestamptz,
  add column if not exists marketplace_suspension_reason text;

alter table public.profiles
  drop constraint if exists profiles_marketplace_suspension_reason_check;
alter table public.profiles
  add constraint profiles_marketplace_suspension_reason_check
  check (
    marketplace_suspension_reason is null
    or char_length(marketplace_suspension_reason) between 3 and 300
  );

alter table public.shared_template_moderation_queue
  add column if not exists reviewed_at timestamptz,
  add column if not exists reviewed_by_user_id uuid,
  add column if not exists review_note text,
  add column if not exists resolution text;

alter table public.shared_template_moderation_queue
  drop constraint if exists shared_template_moderation_queue_review_note_check;
alter table public.shared_template_moderation_queue
  add constraint shared_template_moderation_queue_review_note_check
  check (review_note is null or char_length(review_note) between 3 and 300);

create table if not exists public.marketplace_moderation_actions (
  id uuid primary key default gen_random_uuid(),
  template_id uuid not null,
  owner_id uuid not null,
  actor_user_id uuid not null,
  action text not null check (action in (
    'dismiss_reports', 'withdraw_listing', 'restore_listing',
    'suspend_creator_marketplace', 'restore_creator_marketplace'
  )),
  note text not null check (char_length(note) between 3 and 300),
  previous_template_status text,
  resulting_template_status text,
  resulting_review_status text not null,
  created_at timestamptz not null default now()
);

create index if not exists marketplace_moderation_actions_template_created_idx
  on public.marketplace_moderation_actions (template_id, created_at desc);
create index if not exists marketplace_moderation_actions_owner_created_idx
  on public.marketplace_moderation_actions (owner_id, created_at desc);

alter table public.marketplace_moderation_actions enable row level security;
revoke all on public.marketplace_moderation_actions from anon, authenticated;

-- A marketplace suspension is enforced beneath the UI.  The trigger covers
-- publication, purchase, favourites and reports without having to trust a
-- browser-side flag.  Database administrators can still carry out review
-- actions and restore a member.
create or replace function public.enforce_marketplace_member_active()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  v_member_id uuid;
begin
  if public.is_beta_admin() then
    return new;
  end if;

  v_member_id := nullif(to_jsonb(new) ->> tg_argv[0], '')::uuid;
  if v_member_id is not null and exists (
    select 1
    from public.profiles profile
    where profile.id = v_member_id
      and profile.marketplace_suspended_at is not null
  ) then
    raise exception 'marketplace_member_suspended' using errcode = '42501';
  end if;
  return new;
end;
$$;

drop trigger if exists shared_templates_enforce_marketplace_member_active on public.shared_templates;
create trigger shared_templates_enforce_marketplace_member_active
before insert or update on public.shared_templates
for each row execute function public.enforce_marketplace_member_active('owner_id');

drop trigger if exists shared_template_redemptions_enforce_marketplace_member_active on public.shared_template_redemptions;
create trigger shared_template_redemptions_enforce_marketplace_member_active
before insert on public.shared_template_redemptions
for each row execute function public.enforce_marketplace_member_active('consumer_id');

drop trigger if exists shared_template_favorites_enforce_marketplace_member_active on public.shared_template_favorites;
create trigger shared_template_favorites_enforce_marketplace_member_active
before insert on public.shared_template_favorites
for each row execute function public.enforce_marketplace_member_active('user_id');

drop trigger if exists shared_template_reports_enforce_marketplace_member_active on public.shared_template_reports;
create trigger shared_template_reports_enforce_marketplace_member_active
before insert on public.shared_template_reports
for each row execute function public.enforce_marketplace_member_active('reporter_id');

-- Administrators need to inspect every queued preview, including a withdrawn
-- listing.  Normal users retain the existing owner/purchased/public rules.
create or replace function public.shared_template_asset_is_readable(p_name text)
returns boolean
language sql
stable
security definer
set search_path = public, storage
as $$
  select auth.uid() is not null and (
    public.is_beta_admin()
    or exists (
      select 1 from public.shared_templates t
      where (
        t.owner_id = auth.uid()
        or (t.status = 'published' and t.preview_path = p_name)
        or (
          t.style_reference_path = p_name
          and exists (
            select 1 from public.shared_template_redemptions r
            where r.template_id = t.id and r.consumer_id = auth.uid()
          )
        )
      ) and (t.preview_path = p_name or t.style_reference_path = p_name)
    )
  );
$$;

create or replace function public.admin_list_marketplace_moderation_queue(
  p_status text default 'pending_review',
  p_limit integer default 50
)
returns table (
  template_id uuid,
  title text,
  description text,
  apply_cost integer,
  tags text[],
  preview_path text,
  template_status text,
  owner_id uuid,
  owner_email text,
  owner_name text,
  owner_marketplace_suspended boolean,
  owner_suspended_at timestamptz,
  report_count integer,
  review_status text,
  first_reported_at timestamptz,
  last_reported_at timestamptz,
  reviewed_at timestamptz,
  review_note text,
  resolution text
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_status text := case
    when p_status in ('pending_review', 'reviewed', 'dismissed', 'all') then p_status
    else 'pending_review'
  end;
  v_limit integer := greatest(1, least(coalesce(p_limit, 50), 100));
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;

  return query
  select
    template.id,
    template.title,
    template.description,
    template.apply_cost,
    template.tags,
    template.preview_path,
    template.status,
    template.owner_id,
    coalesce(owner_profile.email, '')::text,
    coalesce(owner_profile.display_name, '')::text,
    (owner_profile.marketplace_suspended_at is not null),
    owner_profile.marketplace_suspended_at,
    queue.report_count,
    queue.review_status,
    queue.first_reported_at,
    queue.last_reported_at,
    queue.reviewed_at,
    queue.review_note,
    queue.resolution
  from public.shared_template_moderation_queue queue
  join public.shared_templates template on template.id = queue.template_id
  left join public.profiles owner_profile on owner_profile.id = template.owner_id
  where v_status = 'all' or queue.review_status = v_status
  order by
    case when queue.review_status = 'pending_review' then 0 else 1 end,
    queue.last_reported_at desc,
    template.updated_at desc
  limit v_limit;
end;
$$;

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

  update public.shared_template_moderation_queue
  set review_status = v_review_status,
      reviewed_at = now(),
      reviewed_by_user_id = v_actor,
      review_note = v_note,
      resolution = v_resolution
  where template_id = v_template.id;

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

  return query select v_template.id, v_template.status, v_review_status, coalesce(v_suspended, false);
end;
$$;

create or replace function public.admin_list_marketplace_moderation_actions(
  p_limit integer default 100
)
returns table (
  action_id uuid,
  template_id uuid,
  template_title text,
  owner_email text,
  action text,
  note text,
  previous_template_status text,
  resulting_template_status text,
  resulting_review_status text,
  operator_label text,
  occurred_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  return query
  select
    action_log.id,
    action_log.template_id,
    coalesce(template.title, '已移除膠卷')::text,
    coalesce(owner_profile.email, '')::text,
    action_log.action,
    action_log.note,
    action_log.previous_template_status,
    action_log.resulting_template_status,
    action_log.resulting_review_status,
    coalesce(actor_profile.email, '管理員')::text,
    action_log.created_at
  from public.marketplace_moderation_actions action_log
  left join public.shared_templates template on template.id = action_log.template_id
  left join public.profiles owner_profile on owner_profile.id = action_log.owner_id
  left join public.profiles actor_profile on actor_profile.id = action_log.actor_user_id
  order by action_log.created_at desc
  limit greatest(1, least(coalesce(p_limit, 100), 200));
end;
$$;

revoke all on function public.enforce_marketplace_member_active() from public, anon;
revoke all on function public.shared_template_asset_is_readable(text) from public, anon;
revoke all on function public.admin_list_marketplace_moderation_queue(text, integer) from public, anon;
revoke all on function public.admin_moderate_shared_template(uuid, text, text) from public, anon;
revoke all on function public.admin_list_marketplace_moderation_actions(integer) from public, anon;
grant execute on function public.shared_template_asset_is_readable(text) to authenticated;
grant execute on function public.admin_list_marketplace_moderation_queue(text, integer) to authenticated;
grant execute on function public.admin_moderate_shared_template(uuid, text, text) to authenticated;
grant execute on function public.admin_list_marketplace_moderation_actions(integer) to authenticated;

notify pgrst, 'reload schema';
