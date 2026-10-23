-- Sticker Studio Next: administrator-only member entitlement inspection.
--
-- The console deliberately reads through security-definer RPCs rather than
-- granting an admin browser direct access to member, payment, or credit
-- tables.  Each function verifies the caller is in public.admin_users.

create or replace function public.admin_get_member_credit_lots(p_user_id uuid)
returns table (
  source_kind text,
  product_code text,
  total_credits integer,
  remaining_credits integer,
  starts_at timestamptz,
  expires_at timestamptz,
  lot_status text
)
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  if p_user_id is null or not exists (
    select 1 from public.profiles p where p.id = p_user_id
  ) then
    raise exception 'member_not_found' using errcode = 'P0002';
  end if;

  return query
  select
    coalesce(g.source_kind, 'legacy')::text,
    coalesce(g.product_code, '')::text,
    g.total_credits,
    g.remaining_credits,
    g.starts_at,
    g.expires_at,
    case
      when g.expires_at <= now() then '已到期'
      when g.starts_at > now() then '尚未生效'
      when g.remaining_credits <= 0 then '已用完'
      else '可用'
    end::text
  from public.image_credit_grants g
  where g.user_id = p_user_id
  order by
    case when g.expires_at > now() and g.remaining_credits > 0 then 0 else 1 end,
    g.expires_at asc,
    g.created_at desc
  limit 100;
end;
$$;

create or replace function public.admin_get_member_entitlements(p_user_id uuid)
returns table (
  entitlement_kind text,
  product_code text,
  daily_copy_limit integer,
  library_capacity integer,
  starts_at timestamptz,
  expires_at timestamptz,
  entitlement_status text,
  note text
)
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  if p_user_id is null or not exists (
    select 1 from public.profiles p where p.id = p_user_id
  ) then
    raise exception 'member_not_found' using errcode = 'P0002';
  end if;

  return query
  select
    e.kind,
    coalesce(e.product_code, ''),
    e.daily_copy_limit,
    coalesce(e.library_capacity, 0),
    e.starts_at,
    e.expires_at,
    case
      when e.expires_at <= now() then '已到期'
      when e.starts_at > now() then '尚未生效'
      else '有效'
    end::text,
    e.note
  from public.member_entitlements e
  where e.user_id = p_user_id
  order by e.expires_at desc, e.created_at desc
  limit 100;
end;
$$;

create or replace function public.admin_list_member_operations(
  p_user_id uuid,
  p_limit integer default 50
)
returns table (
  occurred_at timestamptz,
  operation_type text,
  detail text,
  credit_delta integer,
  operator_label text
)
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  if p_user_id is null or not exists (
    select 1 from public.profiles p where p.id = p_user_id
  ) then
    raise exception 'member_not_found' using errcode = 'P0002';
  end if;

  return query
  select *
  from (
    select
      a.created_at as occurred_at,
      '管理員補／扣次'::text as operation_type,
      a.note as detail,
      a.delta as credit_delta,
      coalesce(actor.email, '管理員')::text as operator_label
    from public.credit_admin_adjustments a
    left join public.profiles actor on actor.id = a.actor_user_id
    where a.target_user_id = p_user_id

    union all

    select
      o.created_at,
      case when o.payment_provider = 'admin_test' then '管理員測試購買' else '方案訂單' end::text,
      concat(o.product_code, '｜', o.status, '｜NT$', o.amount_twd)::text,
      0::integer,
      case when o.payment_provider = 'admin_test' then '管理員' else '系統' end::text
    from public.commerce_orders o
    where o.user_id = p_user_id

    union all

    select
      r.redeemed_at,
      '推薦碼兌換'::text,
      concat('推薦碼：', c.code)::text,
      r.reward_credits,
      '會員本人'::text
    from public.referral_code_redemptions r
    join public.referral_codes c on c.id = r.code_id
    where r.redeemer_user_id = p_user_id

    union all

    select
      r.created_at,
      '市集膠卷購入'::text,
      concat('購入：', coalesce(t.title, '已刪除膠卷'))::text,
      -r.charged_cost,
      '會員本人'::text
    from public.shared_template_redemptions r
    left join public.shared_templates t on t.id = r.template_id
    where r.consumer_id = p_user_id
  ) operation_log
  order by occurred_at desc
  limit least(greatest(coalesce(p_limit, 50), 1), 100);
end;
$$;

revoke all on function public.admin_get_member_credit_lots(uuid) from public, anon;
revoke all on function public.admin_get_member_entitlements(uuid) from public, anon;
revoke all on function public.admin_list_member_operations(uuid, integer) from public, anon;

grant execute on function public.admin_get_member_credit_lots(uuid) to authenticated;
grant execute on function public.admin_get_member_entitlements(uuid) to authenticated;
grant execute on function public.admin_list_member_operations(uuid, integer) to authenticated;

notify pgrst, 'reload schema';
