-- A free member is represented by the fallback row returned from
-- get_my_effective_entitlement().  The former simple CASE had no branch for
-- that row (or an historical product code), which raised `case not found` and
-- prevented the private Creator Reel shelf from rendering at all.
--
-- Keep the universal 20 uncollected reels / 15-day retention defaults, and
-- only elevate collected-reel and marketplace rights for recognised active
-- packs.  Unknown legacy codes deliberately receive the safe free defaults.

create or replace function public.get_my_creator_reel_library_status()
returns table (
  eligible boolean,
  marketplace_eligible boolean,
  product_code text,
  auto_reel_limit integer,
  retention_days integer,
  collected_reel_limit integer,
  publish_template_limit integer,
  active_auto_reel_count integer,
  collected_reel_count integer,
  unlocked_template_count integer,
  shared_reel_count integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_entitlement record;
  v_auto_limit integer := 20;
  v_collected_limit integer := 5;
  v_publish_limit integer := 0;
  v_marketplace boolean := false;
  v_active_count integer := 0;
  v_collected_count integer := 0;
  v_unlocked_count integer := 0;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  select * into v_entitlement from public.get_my_effective_entitlement();

  -- `free` is a deliberate fallback from get_my_effective_entitlement().
  -- The ELSE also prevents an older, no-longer-sold product from blocking the
  -- entire reel library for that member.
  case coalesce(v_entitlement.product_code, 'free')
    when 'credit_pack_7' then
      v_marketplace := true;
      v_collected_limit := 7;
      v_publish_limit := 3;
    when 'basic_monthly' then
      v_marketplace := true;
      v_collected_limit := 20;
      v_publish_limit := 10;
    when 'standard_monthly' then
      v_marketplace := true;
      v_collected_limit := 30;
      v_publish_limit := 15;
    when 'advanced_monthly' then
      v_marketplace := true;
      v_collected_limit := 40;
      v_publish_limit := 20;
    else
      null;
  end case;

  select count(*)::integer into v_active_count
  from public.creator_reels r
  where r.owner_id = v_user
    and r.status = 'active'
    and r.auto_expires_at is not null
    and r.auto_expires_at > now();

  select count(*)::integer into v_collected_count
  from public.creator_reels r
  where r.owner_id = v_user
    and r.status = 'collected';

  select count(*)::integer into v_unlocked_count
  from public.shared_template_redemptions r
  join public.shared_templates t on t.id = r.template_id
  where r.consumer_id = v_user and t.owner_id <> v_user;

  return query
  select
    true,
    v_marketplace,
    coalesce(v_entitlement.product_code, 'free'),
    v_auto_limit,
    15,
    v_collected_limit,
    v_publish_limit,
    v_active_count,
    v_collected_count,
    v_unlocked_count,
    v_collected_count + v_unlocked_count;
end;
$$;

revoke all on function public.get_my_creator_reel_library_status() from public, anon;
grant execute on function public.get_my_creator_reel_library_status() to authenticated;

notify pgrst, 'reload schema';
