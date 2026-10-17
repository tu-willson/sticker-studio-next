-- Sticker Studio Next: one-time reconciliation of administrator adjustments
-- that were written to the legacy ledger after spendable credit lots existed.

create table if not exists public.admin_credit_adjustment_syncs (
  adjustment_id uuid primary key
    references public.credit_admin_adjustments(id) on delete cascade,
  applied_delta integer not null check (applied_delta <> 0),
  synced_at timestamptz not null default now()
);

alter table public.admin_credit_adjustment_syncs enable row level security;
revoke all on table public.admin_credit_adjustment_syncs from anon, authenticated;

create or replace function public.admin_adjust_member_credits(
  p_user_id uuid, p_delta integer, p_note text
)
returns table (user_id uuid, credit_balance integer)
language plpgsql security definer set search_path = public
as $$
declare
  v_actor uuid := auth.uid(); v_note text := trim(coalesce(p_note, ''));
  v_remaining integer; v_grant record; v_adjustment_id uuid := gen_random_uuid();
begin
  if not public.is_beta_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  if p_user_id is null or p_delta is null or p_delta = 0 or p_delta not between -50 and 50 then
    raise exception 'invalid_credit_adjustment' using errcode = '22023';
  end if;
  if char_length(v_note) not between 3 and 160 then
    raise exception 'invalid_adjustment_note' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(p_user_id::text, 0));
  if not exists (select 1 from public.profiles where id = p_user_id) then
    raise exception 'member_not_found' using errcode = 'P0002';
  end if;
  if p_delta < 0 and public.get_my_credit_balance_for_user(p_user_id) < -p_delta then
    raise exception 'negative_credit_balance_not_allowed' using errcode = '22023';
  end if;
  insert into public.credit_admin_adjustments (id, actor_user_id, target_user_id, delta, note)
  values (v_adjustment_id, v_actor, p_user_id, p_delta, v_note);
  if p_delta > 0 then
    insert into public.image_credit_grants (
      user_id, total_credits, remaining_credits, starts_at, expires_at,
      external_reference, source_kind
    ) values (
      p_user_id, p_delta, p_delta, now(), now() + interval '365 days',
      'admin-adjustment:' || v_adjustment_id::text, 'admin'
    );
  else
    v_remaining := -p_delta;
    for v_grant in
      select id, remaining_credits from public.image_credit_grants
      where user_id = p_user_id and starts_at <= now() and expires_at > now()
        and remaining_credits > 0
      order by expires_at asc, created_at asc for update
    loop
      exit when v_remaining = 0;
      update public.image_credit_grants
      set remaining_credits = remaining_credits - least(remaining_credits, v_remaining)
      where id = v_grant.id;
      v_remaining := v_remaining - least(v_grant.remaining_credits, v_remaining);
    end loop;
  end if;
  insert into public.admin_credit_adjustment_syncs (adjustment_id, applied_delta)
  values (v_adjustment_id, p_delta) on conflict (adjustment_id) do nothing;
  return query select p_user_id, public.get_my_credit_balance_for_user(p_user_id);
end;
$$;

create or replace function public.sync_legacy_admin_credit_adjustments()
returns table (
  synced_positive_rows integer,
  synced_positive_credits integer,
  synced_negative_rows integer,
  synced_negative_credits integer
)
language plpgsql security definer set search_path = public
as $$
declare
  v_adjustment record; v_grant record; v_remaining integer;
  v_positive_rows integer := 0; v_positive_credits integer := 0;
  v_negative_rows integer := 0; v_negative_credits integer := 0;
begin
  if not public.is_beta_admin() then raise exception 'admin_required' using errcode = '42501'; end if;
  for v_adjustment in
    select a.id, a.target_user_id, a.delta
    from public.credit_admin_adjustments a
    where not exists (
      select 1 from public.admin_credit_adjustment_syncs s where s.adjustment_id = a.id
    )
    and (
      a.delta < 0 or not exists (
        select 1 from public.image_credit_grants g
        where g.external_reference = 'admin-adjustment:' || a.id::text
      )
    )
    order by a.created_at, a.id
  loop
    perform pg_advisory_xact_lock(hashtextextended(v_adjustment.target_user_id::text, 0));
    if v_adjustment.delta > 0 then
      insert into public.image_credit_grants (
        user_id, total_credits, remaining_credits, starts_at, expires_at,
        external_reference, source_kind
      ) values (
        v_adjustment.target_user_id, v_adjustment.delta, v_adjustment.delta,
        now(), now() + interval '365 days',
        'admin-adjustment:' || v_adjustment.id::text, 'admin'
      );
      v_positive_rows := v_positive_rows + 1;
      v_positive_credits := v_positive_credits + v_adjustment.delta;
    else
      if public.get_my_credit_balance_for_user(v_adjustment.target_user_id) < -v_adjustment.delta then
        raise exception 'legacy_adjustment_insufficient_balance:%', v_adjustment.id using errcode = '22023';
      end if;
      v_remaining := -v_adjustment.delta;
      for v_grant in
        select id, remaining_credits from public.image_credit_grants
        where user_id = v_adjustment.target_user_id and starts_at <= now()
          and expires_at > now() and remaining_credits > 0
        order by expires_at asc, created_at asc for update
      loop
        exit when v_remaining = 0;
        update public.image_credit_grants
        set remaining_credits = remaining_credits - least(remaining_credits, v_remaining)
        where id = v_grant.id;
        v_remaining := v_remaining - least(v_grant.remaining_credits, v_remaining);
      end loop;
      v_negative_rows := v_negative_rows + 1;
      v_negative_credits := v_negative_credits + v_adjustment.delta;
    end if;
    insert into public.admin_credit_adjustment_syncs (adjustment_id, applied_delta)
    values (v_adjustment.id, v_adjustment.delta);
  end loop;
  return query select v_positive_rows, v_positive_credits, v_negative_rows, v_negative_credits;
end;
$$;

revoke all on function public.sync_legacy_admin_credit_adjustments() from public, anon;
grant execute on function public.admin_adjust_member_credits(uuid, integer, text) to authenticated;
grant execute on function public.sync_legacy_admin_credit_adjustments() to authenticated;

notify pgrst, 'reload schema';
