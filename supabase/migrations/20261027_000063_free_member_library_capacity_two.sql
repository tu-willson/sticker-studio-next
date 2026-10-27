-- Free accounts may retain up to two cloud projects.  Paid entitlement
-- capacities keep their existing values.

create or replace function public.get_my_effective_entitlement()
returns table (
  entitlement_id uuid,
  product_code text,
  entitlement_kind text,
  daily_copy_limit integer,
  library_capacity integer,
  expires_at timestamptz
)
language plpgsql security definer set search_path = public
as $$
declare v_user uuid := auth.uid();
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;

  return query
  select e.id, e.product_code, e.kind, e.daily_copy_limit,
         coalesce(e.library_capacity, 5), e.expires_at
  from public.member_entitlements e
  where e.user_id = v_user and e.kind = 'subscription'
    and e.starts_at <= now() and e.expires_at > now()
  order by e.expires_at desc, e.created_at desc limit 1;
  if found then return; end if;

  return query
  select e.id, e.product_code, e.kind, e.daily_copy_limit,
         coalesce(e.library_capacity, 5), e.expires_at
  from public.member_entitlements e
  where e.user_id = v_user and e.kind = 'credit_pack'
    and e.starts_at <= now() and e.expires_at > now()
  order by e.daily_copy_limit desc, coalesce(e.library_capacity, 5) desc,
           e.expires_at desc, e.created_at desc limit 1;
  if found then return; end if;

  return query select null::uuid, null::text, 'free'::text, 2, 2, null::timestamptz;
end;
$$;

notify pgrst, 'reload schema';
