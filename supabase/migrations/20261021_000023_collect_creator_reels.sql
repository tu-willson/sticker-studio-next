-- Sticker Studio Next: allow a subscriber to promote one automatic reel into
-- a permanent private collection.  This is intentionally separate from the
-- future public-template workflow: no data is made public here.

create or replace function public.collect_creator_reel(p_reel_id uuid)
returns table (
  collected_reel_count integer,
  collected_reel_limit integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_entitlement record;
  v_collected_limit integer := 0;
  v_collected_count integer := 0;
  v_reel public.creator_reels%rowtype;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_reel_id is null then
    raise exception 'invalid_creator_reel' using errcode = '22023';
  end if;

  select * into v_entitlement from public.get_my_effective_entitlement();
  if found and v_entitlement.entitlement_kind = 'subscription' then
    case v_entitlement.product_code
      when 'basic_monthly' then v_collected_limit := 20;
      when 'standard_monthly' then v_collected_limit := 30;
      when 'advanced_monthly' then v_collected_limit := 40;
    end case;
  end if;
  if v_collected_limit = 0 then
    raise exception 'creator_reels_subscription_required' using errcode = '42501';
  end if;

  -- A per-member lock makes the quota correct even if two browser tabs press
  -- Collect at nearly the same time.
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 43));

  select * into v_reel
  from public.creator_reels r
  where r.id = p_reel_id and r.owner_id = v_user
  for update;
  if not found then
    raise exception 'creator_reel_not_found' using errcode = 'P0002';
  end if;
  if v_reel.status <> 'active'
     or v_reel.auto_expires_at is null
     or v_reel.auto_expires_at <= now() then
    raise exception 'creator_reel_not_collectable' using errcode = 'P0002';
  end if;

  select count(*)::integer into v_collected_count
  from public.creator_reels r
  where r.owner_id = v_user and r.status = 'collected';
  if v_collected_count >= v_collected_limit then
    raise exception 'creator_reel_collection_limit' using errcode = '23514';
  end if;

  update public.creator_reels r
  set status = 'collected', auto_expires_at = null
  where r.id = v_reel.id and r.owner_id = v_user;

  return query select v_collected_count + 1, v_collected_limit;
end;
$$;

revoke all on function public.collect_creator_reel(uuid) from public, anon;
grant execute on function public.collect_creator_reel(uuid) to authenticated;

notify pgrst, 'reload schema';
