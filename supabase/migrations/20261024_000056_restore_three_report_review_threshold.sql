-- Testing is complete: restore the normal rule that three independent paid
-- member reports are required before a listing enters the admin review queue.
-- Remove only the temporary one-report test queue entries.  Source report
-- records are deliberately retained for audit purposes.
delete from public.shared_template_moderation_queue
where report_count < 3;

alter table public.shared_template_moderation_queue
  drop constraint if exists shared_template_moderation_queue_report_count_check;
alter table public.shared_template_moderation_queue
  add constraint shared_template_moderation_queue_report_count_check
  check (report_count >= 3);

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
    return query select v_count, v_count >= 3;
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

notify pgrst, 'reload schema';
