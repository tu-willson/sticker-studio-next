-- Let administrators return an active subscriber or 7-credit-pack member to
-- the free-member baseline without touching referral, trial, admin, or
-- marketplace-creator reward credits.

alter table public.member_account_admin_actions
  drop constraint if exists member_account_admin_actions_action_check;
alter table public.member_account_admin_actions
  add constraint member_account_admin_actions_action_check
  check (action in (
    'suspend_account', 'restore_account',
    'suspend_marketplace', 'restore_marketplace',
    'remove_membership_entitlements',
    'delete_private_works', 'delete_account'
  ));

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
       'remove_membership_entitlements',
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
  elsif v_action = 'remove_membership_entitlements' then
    -- Returning to the free baseline revokes only paid-plan rights and their
    -- remaining paid credits.  Non-paid credit sources remain untouched.
    update public.member_entitlements
    set expires_at = least(expires_at, now())
    where user_id = p_user_id
      and kind in ('subscription', 'credit_pack')
      and expires_at > now();
    update public.member_subscriptions
    set status = 'expired',
        current_period_end = least(current_period_end, now())
    where user_id = p_user_id
      and status in ('active', 'past_due');
    update public.image_credit_grants
    set remaining_credits = 0,
        expires_at = least(expires_at, now())
    where user_id = p_user_id
      and source_kind in ('subscription', 'credit_pack')
      and expires_at > now();
  else
    select count(*)::integer into v_library_count
    from public.library_projects where owner_id = p_user_id;
    select count(*)::integer into v_reel_count
    from public.creator_reels where owner_id = p_user_id;
    select count(*)::integer into v_template_count
    from public.shared_templates where owner_id = p_user_id and status <> 'deleted';

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

notify pgrst, 'reload schema';
