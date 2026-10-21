-- Sticker Studio Next: private-template marketplace browsing and one-time
-- unlocks.  A member pays a template's set cost once; the complete cost is
-- granted to the publisher as generation credits.  Reopening an unlocked
-- template never charges again.

alter table public.image_credit_grants
  drop constraint if exists image_credit_grants_source_kind_check;
alter table public.image_credit_grants
  add constraint image_credit_grants_source_kind_check
  check (source_kind in ('legacy', 'trial', 'credit_pack', 'subscription', 'admin', 'template_reward'));

create table public.shared_template_redemptions (
  id uuid primary key default gen_random_uuid(),
  template_id uuid not null references public.shared_templates(id) on delete restrict,
  consumer_id uuid not null references auth.users(id) on delete cascade,
  idempotency_key uuid not null,
  charged_cost integer not null check (charged_cost between 0 and 20),
  snapshot jsonb not null check (jsonb_typeof(snapshot) = 'object'),
  style_reference_path text,
  created_at timestamptz not null default now(),
  unique (consumer_id, idempotency_key),
  unique (consumer_id, template_id)
);

create index shared_template_redemptions_template_idx
  on public.shared_template_redemptions (template_id, created_at desc);

alter table public.shared_template_redemptions enable row level security;
revoke all on public.shared_template_redemptions from anon, authenticated;
grant select on public.shared_template_redemptions to authenticated;
create policy "shared_template_redemptions_select_own"
on public.shared_template_redemptions for select to authenticated
using (consumer_id = (select auth.uid()));

-- Direct table reads would expose immutable settings and, more importantly,
-- the protected style-reference object path.  The RPCs below return only the
-- fields needed by each screen.
revoke select on public.shared_templates from authenticated;

create or replace function public.shared_template_asset_is_readable(p_name text)
returns boolean
language sql
stable
security definer
set search_path = public, storage
as $$
  select auth.uid() is not null and exists (
    select 1 from public.shared_templates t
    where (
      t.owner_id = auth.uid()
      or (t.status = 'published' and t.preview_path = p_name)
      or (
        t.status = 'published'
        and t.style_reference_path = p_name
        and exists (
          select 1 from public.shared_template_redemptions r
          where r.template_id = t.id and r.consumer_id = auth.uid()
        )
      )
    ) and (t.preview_path = p_name or t.style_reference_path = p_name)
  );
$$;

create or replace function public.get_my_shared_template_sources()
returns table (source_reel_id uuid, status text)
language sql
stable
security definer
set search_path = public
as $$
  select t.source_reel_id, t.status
  from public.shared_templates t
  where t.owner_id = auth.uid();
$$;

create or replace function public.list_shared_template_marketplace(
  p_limit integer default 24,
  p_offset integer default 0
)
returns table (
  template_id uuid,
  title text,
  description text,
  apply_cost integer,
  preview_path text,
  has_style_reference boolean,
  published_at timestamptz,
  is_owner boolean,
  is_unlocked boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  return query
  select t.id, t.title, t.description, t.apply_cost, t.preview_path,
    t.style_reference_path is not null, t.published_at,
    t.owner_id = v_user,
    (t.owner_id = v_user or exists (
      select 1 from public.shared_template_redemptions r
      where r.template_id = t.id and r.consumer_id = v_user
    ))
  from public.shared_templates t
  where t.status = 'published'
  order by t.published_at desc, t.id desc
  limit greatest(1, least(coalesce(p_limit, 24), 48))
  offset greatest(0, coalesce(p_offset, 0));
end;
$$;

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
      order by g.expires_at asc, g.created_at asc
      for update
    loop
      exit when v_remaining = 0;
      update public.image_credit_grants g
      set remaining_credits = g.remaining_credits - least(g.remaining_credits, v_remaining)
      where g.id = v_grant.id;
      v_remaining := v_remaining - least(v_grant.remaining_credits, v_remaining);
    end loop;

    -- The publisher receives the entire price as a separate auditable grant.
    insert into public.image_credit_grants (
      user_id, total_credits, remaining_credits, starts_at, expires_at,
      external_reference, source_kind
    ) values (
      v_template.owner_id, v_charge, v_charge, now(), now() + interval '365 days',
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

revoke all on function public.get_my_shared_template_sources() from public, anon;
revoke all on function public.list_shared_template_marketplace(integer, integer) from public, anon;
revoke all on function public.redeem_shared_template(uuid, uuid) from public, anon;
grant execute on function public.get_my_shared_template_sources() to authenticated;
grant execute on function public.list_shared_template_marketplace(integer, integer) to authenticated;
grant execute on function public.redeem_shared_template(uuid, uuid) to authenticated;

notify pgrst, 'reload schema';
