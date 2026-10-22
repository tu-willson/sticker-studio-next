-- Keep report eligibility aligned with the templates a member can still see.
-- Members who unlocked a withdrawn template retain private access and can
-- still report it; the report immediately hides it from their marketplace.

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
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
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

  insert into public.shared_template_reports (template_id, reporter_id)
  values (p_template_id, v_user)
  on conflict (template_id, reporter_id) do nothing;

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

revoke all on function public.report_shared_template(uuid) from public, anon;
grant execute on function public.report_shared_template(uuid) to authenticated;

notify pgrst, 'reload schema';
