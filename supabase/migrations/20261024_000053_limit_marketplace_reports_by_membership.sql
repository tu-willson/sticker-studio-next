-- Marketplace reports are a paid-member moderation tool.
-- Active subscribers and active 7-credit-pack holders may submit up to two
-- distinct reports per Taiwan calendar day. Free members cannot report.

create index if not exists shared_template_reports_reporter_created_idx
  on public.shared_template_reports (reporter_id, created_at desc);

create or replace function public.get_my_marketplace_report_status()
returns table (
  can_report boolean,
  daily_limit integer,
  used_count integer,
  remaining_count integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_entitlement record;
  v_today date := (now() at time zone 'Asia/Taipei')::date;
  v_used integer := 0;
  v_allowed boolean := false;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  select * into v_entitlement from public.get_my_effective_entitlement();
  v_allowed := v_entitlement.entitlement_kind = 'subscription'
    or v_entitlement.product_code = 'credit_pack_7';

  select count(*)::integer into v_used
  from public.shared_template_reports report
  where report.reporter_id = v_user
    and (report.created_at at time zone 'Asia/Taipei')::date = v_today;

  return query
  select v_allowed, case when v_allowed then 2 else 0 end, v_used,
    case when v_allowed then greatest(2 - v_used, 0) else 0 end;
end;
$$;

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
  from public.shared_templates template
  where template.id = p_template_id
    and (
      template.status = 'published'
      or exists (
        select 1
        from public.shared_template_redemptions redemption
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

  -- Reporting a card already reported by this member is idempotent and does
  -- not consume another daily report slot.
  if exists (
    select 1 from public.shared_template_reports report
    where report.template_id = p_template_id and report.reporter_id = v_user
  ) then
    select count(*)::integer into v_count
    from public.shared_template_reports report
    where report.template_id = p_template_id;
    return query select v_count, v_count >= 3;
    return;
  end if;

  select count(*)::integer into v_used
  from public.shared_template_reports report
  where report.reporter_id = v_user
    and (report.created_at at time zone 'Asia/Taipei')::date = v_today;
  if v_used >= 2 then
    raise exception 'marketplace_report_daily_limit_reached' using errcode = '22023';
  end if;

  insert into public.shared_template_reports (template_id, reporter_id)
  values (p_template_id, v_user);

  select count(*)::integer into v_count
  from public.shared_template_reports report
  where report.template_id = p_template_id;

  if v_count >= 3 then
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

  return query select v_count, v_count >= 3;
end;
$$;

revoke all on function public.get_my_marketplace_report_status() from public, anon;
revoke all on function public.report_shared_template(uuid) from public, anon;
grant execute on function public.get_my_marketplace_report_status() to authenticated;
grant execute on function public.report_shared_template(uuid) to authenticated;

notify pgrst, 'reload schema';
