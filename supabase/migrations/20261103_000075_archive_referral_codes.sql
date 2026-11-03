-- Referral codes with successful redemptions are linked to both the redemption
-- and the issued credit lot, so physical deletion would break the audit trail.
-- "Delete" therefore means archive: the code disappears from the active admin
-- list only after an administrator has first disabled it.

alter table public.referral_codes
  add column if not exists archived_at timestamptz,
  add column if not exists archived_by uuid references auth.users(id) on delete restrict;

create index if not exists referral_codes_visible_created_idx
  on public.referral_codes (created_at desc)
  where archived_at is null;

create or replace function public.admin_archive_referral_code(p_code_id uuid)
returns table (code_id uuid, archived_at timestamptz)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_actor uuid := auth.uid();
  v_archived_id uuid;
  v_archived_at timestamptz;
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  if p_code_id is null then
    raise exception 'invalid_referral_code_archive' using errcode = '22023';
  end if;

  -- The condition is intentionally in the update statement, so the code cannot
  -- be archived if a concurrent administrator re-enables it first.
  update public.referral_codes c
  set archived_at = now(),
      archived_by = v_actor
  where c.id = p_code_id
    and c.is_active = false
    and c.archived_at is null
  returning c.id, c.archived_at into v_archived_id, v_archived_at;

  if not found then
    if exists (
      select 1 from public.referral_codes c
      where c.id = p_code_id and c.archived_at is null and c.is_active
    ) then
      raise exception 'referral_code_must_be_inactive' using errcode = '22023';
    end if;
    raise exception 'referral_code_not_found' using errcode = 'P0002';
  end if;

  return query select v_archived_id, v_archived_at;
end;
$$;

drop function if exists public.admin_list_referral_codes();
create function public.admin_list_referral_codes()
returns table (
  code_id uuid,
  code text,
  redemption_limit integer,
  reward_credits integer,
  redeemed_count integer,
  is_active boolean,
  expires_at timestamptz,
  created_at timestamptz,
  note text,
  requires_paid_membership boolean
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
    c.id, c.code, c.redemption_limit, c.reward_credits,
    count(r.id)::integer, c.is_active, c.expires_at, c.created_at, c.note,
    c.requires_paid_membership
  from public.referral_codes c
  left join public.referral_code_redemptions r on r.code_id = c.id
  where c.archived_at is null
  group by c.id
  order by c.created_at desc;
end;
$$;

revoke all on function public.admin_archive_referral_code(uuid) from public, anon;
revoke all on function public.admin_list_referral_codes() from public, anon;
grant execute on function public.admin_archive_referral_code(uuid) to authenticated;
grant execute on function public.admin_list_referral_codes() to authenticated;

notify pgrst, 'reload schema';
