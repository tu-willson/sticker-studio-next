-- Sticker Studio Next: administrator-controlled member account governance.
--
-- An account deletion is deliberately a privacy-preserving soft deletion:
-- access is disabled, private work is erased, public listings are removed,
-- but immutable purchaser snapshots and admin audit evidence remain intact.
-- Deleting auth.users directly would break retained order/audit relations.

alter table public.profiles
  add column if not exists account_suspended_at timestamptz,
  add column if not exists account_suspension_reason text,
  add column if not exists account_deleted_at timestamptz,
  add column if not exists account_deletion_reason text;

create table if not exists public.member_account_admin_actions (
  id uuid primary key default gen_random_uuid(),
  actor_user_id uuid not null references auth.users(id) on delete restrict,
  target_user_id uuid not null references auth.users(id) on delete restrict,
  target_email_snapshot text not null default '',
  action text not null check (action in (
    'suspend_account', 'restore_account',
    'suspend_marketplace', 'restore_marketplace',
    'delete_private_works', 'delete_account'
  )),
  note text not null check (char_length(note) between 3 and 300),
  affected_library_projects integer not null default 0,
  affected_creator_reels integer not null default 0,
  affected_shared_templates integer not null default 0,
  created_at timestamptz not null default now()
);

create index if not exists member_account_admin_actions_target_created_idx
  on public.member_account_admin_actions (target_user_id, created_at desc);

alter table public.member_account_admin_actions enable row level security;
revoke all on table public.member_account_admin_actions from anon, authenticated;

create or replace function public.get_my_account_access()
returns table (is_active boolean, account_state text, reason text)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_profile public.profiles%rowtype;
begin
  if v_user is null then
    return query select false, 'signed_out'::text, ''::text;
    return;
  end if;

  select * into v_profile
  from public.profiles
  where id = v_user;

  if not found or v_profile.account_deleted_at is not null then
    return query select false, 'deleted'::text,
      coalesce(v_profile.account_deletion_reason, '')::text;
  elsif v_profile.account_suspended_at is not null then
    return query select false, 'suspended'::text,
      coalesce(v_profile.account_suspension_reason, '')::text;
  end if;

  return query select true, 'active'::text, ''::text;
end;
$$;

create or replace function public.admin_get_member_governance_status(p_user_id uuid)
returns table (
  account_suspended boolean,
  account_suspended_at timestamptz,
  account_suspension_reason text,
  account_deleted boolean,
  account_deleted_at timestamptz,
  account_deletion_reason text,
  marketplace_restricted boolean,
  marketplace_restricted_at timestamptz,
  marketplace_restriction_reason text,
  library_project_count integer,
  creator_reel_count integer,
  shared_template_count integer
)
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  if p_user_id is null then
    raise exception 'member_required' using errcode = '22023';
  end if;

  return query
  select
    profile.account_suspended_at is not null,
    profile.account_suspended_at,
    coalesce(profile.account_suspension_reason, ''),
    profile.account_deleted_at is not null,
    profile.account_deleted_at,
    coalesce(profile.account_deletion_reason, ''),
    profile.marketplace_suspended_at is not null,
    profile.marketplace_suspended_at,
    coalesce(profile.marketplace_suspension_reason, ''),
    (select count(*)::integer from public.library_projects project where project.owner_id = profile.id),
    (select count(*)::integer from public.creator_reels reel where reel.owner_id = profile.id),
    (select count(*)::integer from public.shared_templates template where template.owner_id = profile.id and template.status <> 'deleted')
  from public.profiles profile
  where profile.id = p_user_id;
end;
$$;

create or replace function public.admin_list_member_account_actions(
  p_user_id uuid,
  p_limit integer default 30
)
returns table (
  action text,
  note text,
  created_at timestamptz,
  operator_email text,
  affected_library_projects integer,
  affected_creator_reels integer,
  affected_shared_templates integer
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
    event.action,
    event.note,
    event.created_at,
    coalesce(operator_profile.email, '')::text,
    event.affected_library_projects,
    event.affected_creator_reels,
    event.affected_shared_templates
  from public.member_account_admin_actions event
  left join public.profiles operator_profile on operator_profile.id = event.actor_user_id
  where event.target_user_id = p_user_id
  order by event.created_at desc
  limit greatest(1, least(coalesce(p_limit, 30), 100));
end;
$$;

create or replace function public.admin_manage_member_account(
  p_user_id uuid,
  p_action text,
  p_note text
)
returns table (
  account_suspended boolean,
  account_deleted boolean,
  marketplace_restricted boolean,
  affected_library_projects integer,
  affected_creator_reels integer,
  affected_shared_templates integer
)
language plpgsql
security definer
set search_path = public, storage
as $$
declare
  v_actor uuid := auth.uid();
  v_target public.profiles%rowtype;
  v_action text := trim(coalesce(p_action, ''));
  v_note text := trim(coalesce(p_note, ''));
  v_library_count integer := 0;
  v_reel_count integer := 0;
  v_template_count integer := 0;
  v_account_suspended boolean := false;
  v_account_deleted boolean := false;
  v_marketplace_restricted boolean := false;
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  if p_user_id is null
     or v_action not in (
       'suspend_account', 'restore_account',
       'suspend_marketplace', 'restore_marketplace',
       'delete_private_works', 'delete_account'
     )
     or char_length(v_note) not between 3 and 300 then
    raise exception 'invalid_member_governance_action' using errcode = '22023';
  end if;
  if p_user_id = v_actor or exists (
    select 1 from public.admin_users admin where admin.user_id = p_user_id
  ) then
    raise exception 'administrator_target_protected' using errcode = '42501';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(p_user_id::text, 9));
  select * into v_target
  from public.profiles
  where id = p_user_id
  for update;
  if not found then
    raise exception 'member_not_found' using errcode = 'P0002';
  end if;
  if v_target.account_deleted_at is not null and v_action <> 'delete_account' then
    raise exception 'member_account_deleted' using errcode = '42501';
  end if;

  if v_action = 'suspend_account' then
    update public.profiles
    set account_suspended_at = now(),
        account_suspension_reason = v_note,
        marketplace_suspended_at = now(),
        marketplace_suspension_reason = v_note
    where id = p_user_id;
    update public.shared_templates
    set status = 'withdrawn'
    where owner_id = p_user_id and status = 'published';
  elsif v_action = 'restore_account' then
    update public.profiles
    set account_suspended_at = null,
        account_suspension_reason = null
    where id = p_user_id;
  elsif v_action = 'suspend_marketplace' then
    update public.profiles
    set marketplace_suspended_at = now(),
        marketplace_suspension_reason = v_note
    where id = p_user_id;
    update public.shared_templates
    set status = 'withdrawn'
    where owner_id = p_user_id and status = 'published';
  elsif v_action = 'restore_marketplace' then
    update public.profiles
    set marketplace_suspended_at = null,
        marketplace_suspension_reason = null
    where id = p_user_id;
  else
    select count(*)::integer into v_library_count
    from public.library_projects where owner_id = p_user_id;
    select count(*)::integer into v_reel_count
    from public.creator_reels where owner_id = p_user_id;
    select count(*)::integer into v_template_count
    from public.shared_templates where owner_id = p_user_id and status <> 'deleted';

    -- Purchasers retain their immutable snapshot.  Original listings are
    -- hidden and detached from their source reel before the private reels go.
    update public.shared_templates
    set status = 'deleted', source_reel_id = null
    where owner_id = p_user_id and status <> 'deleted';

    delete from storage.objects
    where bucket_id in ('member-projects', 'creator-reels')
      and name like p_user_id::text || '/%';
    delete from public.library_projects where owner_id = p_user_id;
    delete from public.creator_reels where owner_id = p_user_id;

    if v_action = 'delete_account' then
      update public.profiles
      set account_suspended_at = now(),
          account_suspension_reason = v_note,
          account_deleted_at = now(),
          account_deletion_reason = v_note,
          marketplace_suspended_at = now(),
          marketplace_suspension_reason = v_note,
          display_name = '已刪除帳號',
          email = 'deleted+' || id::text || '@redacted.invalid'
      where id = p_user_id;
    end if;
  end if;

  select
    profile.account_suspended_at is not null,
    profile.account_deleted_at is not null,
    profile.marketplace_suspended_at is not null
  into v_account_suspended, v_account_deleted, v_marketplace_restricted
  from public.profiles profile
  where profile.id = p_user_id;

  insert into public.member_account_admin_actions (
    actor_user_id, target_user_id, target_email_snapshot, action, note,
    affected_library_projects, affected_creator_reels, affected_shared_templates
  ) values (
    v_actor, p_user_id, coalesce(v_target.email, ''), v_action, v_note,
    v_library_count, v_reel_count, v_template_count
  );

  return query select
    coalesce(v_account_suspended, false),
    coalesce(v_account_deleted, false),
    coalesce(v_marketplace_restricted, false),
    v_library_count, v_reel_count, v_template_count;
end;
$$;

revoke all on function public.get_my_account_access() from public, anon;
revoke all on function public.admin_get_member_governance_status(uuid) from public, anon;
revoke all on function public.admin_list_member_account_actions(uuid, integer) from public, anon;
revoke all on function public.admin_manage_member_account(uuid, text, text) from public, anon;
grant execute on function public.get_my_account_access() to authenticated;
grant execute on function public.admin_get_member_governance_status(uuid) to authenticated;
grant execute on function public.admin_list_member_account_actions(uuid, integer) to authenticated;
grant execute on function public.admin_manage_member_account(uuid, text, text) to authenticated;

notify pgrst, 'reload schema';
