-- Fix PL/pgSQL name resolution: `user_id` is also an OUT column of the
-- function, so the ledger column must be explicitly qualified.
create or replace function public.admin_adjust_member_credits(
  p_user_id uuid,
  p_delta integer,
  p_note text
)
returns table (user_id uuid, credit_balance integer)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_actor uuid := auth.uid();
  v_note text := trim(coalesce(p_note, ''));
  v_balance integer;
  v_adjustment_id uuid := gen_random_uuid();
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  if p_user_id is null or p_delta is null or p_delta = 0 or p_delta not between -50 and 50 then
    raise exception 'invalid_credit_adjustment' using errcode = '22023';
  end if;
  if char_length(v_note) < 3 or char_length(v_note) > 160 then
    raise exception 'invalid_adjustment_note' using errcode = '22023';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(p_user_id::text, 0));

  if not exists (select 1 from public.profiles where id = p_user_id) then
    raise exception 'member_not_found' using errcode = 'P0002';
  end if;

  select coalesce(sum(l.delta), 0)::integer into v_balance
  from public.credit_ledger as l
  where l.user_id = p_user_id;

  if v_balance + p_delta < 0 then
    raise exception 'negative_credit_balance_not_allowed' using errcode = '22023';
  end if;

  insert into public.credit_admin_adjustments (
    id, actor_user_id, target_user_id, delta, note
  ) values (
    v_adjustment_id, v_actor, p_user_id, p_delta, v_note
  );

  insert into public.credit_ledger (user_id, delta, reason, reference_id)
  values (p_user_id, p_delta, 'admin_adjustment', v_adjustment_id);

  return query select p_user_id, v_balance + p_delta;
end;
$$;
