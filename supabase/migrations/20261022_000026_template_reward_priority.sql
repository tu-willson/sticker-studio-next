-- Template rewards stay valid for 90 days and are consumed only after a
-- member's normal subscription / purchased credit lots.

-- Align the small number of pre-policy test rewards with the same 90-day
-- lifetime without extending any existing expiry.
update public.image_credit_grants
set expires_at = least(expires_at, created_at + interval '90 days')
where source_kind = 'template_reward';

create or replace function public.redeem_shared_template(
  p_template_id uuid,
  p_idempotency_key uuid
)
returns table (
  template_id uuid,
  title text,
  charged_cost integer,
  remaining_credits integer,
  template_snapshot jsonb,
  style_reference_path text,
  already_unlocked boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_template public.shared_templates%rowtype;
  v_existing public.shared_template_redemptions%rowtype;
  v_balance integer := 0;
  v_remaining integer := 0;
  v_grant record;
  v_charge integer := 0;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_template_id is null or p_idempotency_key is null then
    raise exception 'invalid_shared_template_redemption' using errcode = '22023';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 53));

  select * into v_existing from public.shared_template_redemptions r
  where r.consumer_id = v_user and r.template_id = p_template_id
  for update;
  if found then
    return query select v_existing.template_id,
      (select t.title from public.shared_templates t where t.id = v_existing.template_id),
      0, public.get_my_credit_balance_for_user(v_user), v_existing.snapshot,
      v_existing.style_reference_path, true;
    return;
  end if;

  select * into v_template from public.shared_templates t
  where t.id = p_template_id and t.status = 'published'
  for update;
  if not found then
    raise exception 'shared_template_not_available' using errcode = 'P0002';
  end if;

  if v_template.owner_id <> v_user then
    v_charge := v_template.apply_cost;
    v_balance := public.get_my_credit_balance_for_user(v_user);
    if v_balance < v_charge then
      raise exception 'shared_template_insufficient_credits' using errcode = '22023';
    end if;

    v_remaining := v_charge;
    for v_grant in
      select g.id, g.remaining_credits
      from public.image_credit_grants g
      where g.user_id = v_user and g.starts_at <= now() and g.expires_at > now()
        and g.remaining_credits > 0
      order by case when g.source_kind = 'template_reward' then 1 else 0 end,
        g.expires_at asc, g.created_at asc
      for update
    loop
      exit when v_remaining = 0;
      update public.image_credit_grants g
      set remaining_credits = g.remaining_credits - least(g.remaining_credits, v_remaining)
      where g.id = v_grant.id;
      v_remaining := v_remaining - least(v_grant.remaining_credits, v_remaining);
    end loop;

    insert into public.image_credit_grants (
      user_id, total_credits, remaining_credits, starts_at, expires_at,
      external_reference, source_kind
    ) values (
      v_template.owner_id, v_charge, v_charge, now(), now() + interval '90 days',
      'template-reward:' || p_template_id::text || ':' || v_user::text,
      'template_reward'
    );
  end if;

  insert into public.shared_template_redemptions (
    template_id, consumer_id, idempotency_key, charged_cost, snapshot,
    style_reference_path
  ) values (
    v_template.id, v_user, p_idempotency_key, v_charge, v_template.snapshot,
    v_template.style_reference_path
  );

  return query select v_template.id, v_template.title, v_charge,
    public.get_my_credit_balance_for_user(v_user), v_template.snapshot,
    v_template.style_reference_path, false;
end;
$$;

revoke all on function public.redeem_shared_template(uuid, uuid) from public, anon;
grant execute on function public.redeem_shared_template(uuid, uuid) to authenticated;

notify pgrst, 'reload schema';
