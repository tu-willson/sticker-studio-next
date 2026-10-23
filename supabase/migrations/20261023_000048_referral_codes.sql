-- Sticker Studio Next: audited, capped referral-code rewards.
--
-- Security model:
-- * Only a database-authorized administrator can create or pause a code.
-- * A member can redeem at most one code for the lifetime of their account.
-- * The code capacity, redemption record and credit grant are written in one
--   transaction, so concurrent clicks cannot exceed the configured limit.

alter table public.image_credit_grants
  drop constraint if exists image_credit_grants_source_kind_check;

alter table public.image_credit_grants
  add constraint image_credit_grants_source_kind_check
  check (source_kind in (
    'legacy', 'trial', 'credit_pack', 'subscription', 'admin',
    'template_reward', 'referral'
  ));

create table public.referral_codes (
  id uuid primary key default gen_random_uuid(),
  code text not null unique
    check (code = upper(code) and code ~ '^[A-Z0-9]{4,24}$'),
  redemption_limit integer not null check (redemption_limit between 1 and 100000),
  reward_credits integer not null check (reward_credits between 1 and 100),
  is_active boolean not null default true,
  expires_at timestamptz,
  note text not null default '' check (char_length(note) <= 160),
  created_by uuid not null references auth.users(id) on delete restrict,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deactivated_at timestamptz,
  deactivated_by uuid references auth.users(id) on delete restrict,
  check (expires_at is null or expires_at > created_at)
);

create index referral_codes_active_expires_idx
  on public.referral_codes (is_active, expires_at, created_at desc);

create trigger referral_codes_set_updated_at
before update on public.referral_codes
for each row execute function public.set_updated_at();

create table public.referral_code_redemptions (
  id uuid primary key default gen_random_uuid(),
  code_id uuid not null references public.referral_codes(id) on delete restrict,
  redeemer_user_id uuid not null references auth.users(id) on delete cascade,
  reward_credits integer not null check (reward_credits between 1 and 100),
  credit_grant_id uuid not null references public.image_credit_grants(id) on delete restrict,
  redeemed_at timestamptz not null default now(),
  unique (redeemer_user_id),
  unique (credit_grant_id)
);

create index referral_code_redemptions_code_redeemed_idx
  on public.referral_code_redemptions (code_id, redeemed_at desc);

alter table public.referral_codes enable row level security;
alter table public.referral_code_redemptions enable row level security;
revoke all on public.referral_codes, public.referral_code_redemptions
  from anon, authenticated;

create or replace function public.redeem_referral_code(p_code text)
returns table (
  code text,
  awarded_credits integer,
  credit_balance integer,
  remaining_redemptions integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_code_text text := upper(trim(coalesce(p_code, '')));
  v_code public.referral_codes%rowtype;
  v_redemption_id uuid := gen_random_uuid();
  v_grant_id uuid := gen_random_uuid();
  v_redeemed_count integer := 0;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if v_code_text !~ '^[A-Z0-9]{4,24}$' then
    raise exception 'invalid_referral_code_format' using errcode = '22023';
  end if;

  -- One account is allowed one successful redemption, regardless of code.
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 481516));
  if exists (
    select 1 from public.referral_code_redemptions r
    where r.redeemer_user_id = v_user
  ) then
    raise exception 'referral_already_redeemed' using errcode = '23505';
  end if;

  -- Locking the code serializes its capacity check and its next redemption.
  select * into v_code
  from public.referral_codes c
  where c.code = v_code_text
  for update;
  if not found then
    raise exception 'referral_code_not_found' using errcode = 'P0002';
  end if;
  if not v_code.is_active then
    raise exception 'referral_code_inactive' using errcode = '22023';
  end if;
  if v_code.expires_at is not null and v_code.expires_at <= now() then
    raise exception 'referral_code_expired' using errcode = '22023';
  end if;

  select count(*)::integer into v_redeemed_count
  from public.referral_code_redemptions r
  where r.code_id = v_code.id;
  if v_redeemed_count >= v_code.redemption_limit then
    raise exception 'referral_code_limit_reached' using errcode = '22023';
  end if;

  insert into public.image_credit_grants (
    id, user_id, total_credits, remaining_credits, starts_at, expires_at,
    external_reference, source_kind
  ) values (
    v_grant_id, v_user, v_code.reward_credits, v_code.reward_credits,
    now(), now() + interval '60 days',
    'referral-redemption:' || v_redemption_id::text, 'referral'
  );

  insert into public.referral_code_redemptions (
    id, code_id, redeemer_user_id, reward_credits, credit_grant_id
  ) values (
    v_redemption_id, v_code.id, v_user, v_code.reward_credits, v_grant_id
  );

  return query select
    v_code.code,
    v_code.reward_credits,
    public.get_my_credit_balance(),
    greatest(0, v_code.redemption_limit - v_redeemed_count - 1);
end;
$$;

create or replace function public.get_my_referral_redemption()
returns table (
  code text,
  reward_credits integer,
  redeemed_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
declare v_user uuid := auth.uid();
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  return query
  select c.code, r.reward_credits, r.redeemed_at
  from public.referral_code_redemptions r
  join public.referral_codes c on c.id = r.code_id
  where r.redeemer_user_id = v_user;
end;
$$;

create or replace function public.admin_create_referral_code(
  p_code text,
  p_redemption_limit integer,
  p_reward_credits integer,
  p_expires_at timestamptz default null,
  p_note text default ''
)
returns table (
  code_id uuid,
  code text,
  redemption_limit integer,
  reward_credits integer,
  expires_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_actor uuid := auth.uid();
  v_code text := upper(trim(coalesce(p_code, '')));
  v_note text := trim(coalesce(p_note, ''));
  v_id uuid := gen_random_uuid();
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  if v_code !~ '^[A-Z0-9]{4,24}$' then
    raise exception 'invalid_referral_code_format' using errcode = '22023';
  end if;
  if p_redemption_limit not between 1 and 100000 then
    raise exception 'invalid_referral_limit' using errcode = '22023';
  end if;
  if p_reward_credits not between 1 and 100 then
    raise exception 'invalid_referral_reward' using errcode = '22023';
  end if;
  if char_length(v_note) > 160 then
    raise exception 'invalid_referral_note' using errcode = '22023';
  end if;
  if p_expires_at is not null and p_expires_at <= now() then
    raise exception 'invalid_referral_expiry' using errcode = '22023';
  end if;

  insert into public.referral_codes (
    id, code, redemption_limit, reward_credits, expires_at, note, created_by
  ) values (
    v_id, v_code, p_redemption_limit, p_reward_credits, p_expires_at, v_note, v_actor
  );
  return query select v_id, v_code, p_redemption_limit, p_reward_credits, p_expires_at;
end;
$$;

create or replace function public.admin_set_referral_code_active(
  p_code_id uuid,
  p_is_active boolean
)
returns table (code_id uuid, is_active boolean)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_actor uuid := auth.uid();
  v_updated_id uuid;
  v_updated_active boolean;
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  if p_code_id is null or p_is_active is null then
    raise exception 'invalid_referral_status_update' using errcode = '22023';
  end if;

  update public.referral_codes c
  set is_active = p_is_active,
      deactivated_at = case when p_is_active then null else now() end,
      deactivated_by = case when p_is_active then null else v_actor end
  where c.id = p_code_id
  returning c.id, c.is_active into v_updated_id, v_updated_active;
  if not found then
    raise exception 'referral_code_not_found' using errcode = 'P0002';
  end if;
  return query select v_updated_id, v_updated_active;
end;
$$;

create or replace function public.admin_list_referral_codes()
returns table (
  code_id uuid,
  code text,
  redemption_limit integer,
  reward_credits integer,
  redeemed_count integer,
  is_active boolean,
  expires_at timestamptz,
  created_at timestamptz,
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
  return query
  select
    c.id, c.code, c.redemption_limit, c.reward_credits,
    count(r.id)::integer, c.is_active, c.expires_at, c.created_at, c.note
  from public.referral_codes c
  left join public.referral_code_redemptions r on r.code_id = c.id
  group by c.id
  order by c.created_at desc;
end;
$$;

create or replace function public.admin_list_referral_redemptions(
  p_code_id uuid default null,
  p_limit integer default 100
)
returns table (
  redemption_id uuid,
  code text,
  redeemer_email text,
  redeemer_name text,
  reward_credits integer,
  redeemed_at timestamptz
)
language plpgsql
security definer
set search_path = public
as $$
declare v_limit integer := greatest(1, least(coalesce(p_limit, 100), 200));
begin
  if not public.is_beta_admin() then
    raise exception 'admin_required' using errcode = '42501';
  end if;
  return query
  select r.id, c.code, p.email, p.display_name, r.reward_credits, r.redeemed_at
  from public.referral_code_redemptions r
  join public.referral_codes c on c.id = r.code_id
  join public.profiles p on p.id = r.redeemer_user_id
  where p_code_id is null or r.code_id = p_code_id
  order by r.redeemed_at desc
  limit v_limit;
end;
$$;

revoke all on function public.redeem_referral_code(text) from public, anon;
revoke all on function public.get_my_referral_redemption() from public, anon;
revoke all on function public.admin_create_referral_code(text, integer, integer, timestamptz, text)
  from public, anon;
revoke all on function public.admin_set_referral_code_active(uuid, boolean) from public, anon;
revoke all on function public.admin_list_referral_codes() from public, anon;
revoke all on function public.admin_list_referral_redemptions(uuid, integer) from public, anon;
grant execute on function public.redeem_referral_code(text) to authenticated;
grant execute on function public.get_my_referral_redemption() to authenticated;
grant execute on function public.admin_create_referral_code(text, integer, integer, timestamptz, text)
  to authenticated;
grant execute on function public.admin_set_referral_code_active(uuid, boolean) to authenticated;
grant execute on function public.admin_list_referral_codes() to authenticated;
grant execute on function public.admin_list_referral_redemptions(uuid, integer) to authenticated;

notify pgrst, 'reload schema';
