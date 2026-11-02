-- Marketplace listings may now be free (0 credits) or cost up to 3 credits.
-- Existing live listings are intentionally converted to free listings.

do $$
declare
  v_constraint_name text;
begin
  for v_constraint_name in
    select constraint_row.conname
    from pg_constraint constraint_row
    join pg_attribute attribute_row
      on attribute_row.attrelid = constraint_row.conrelid
     and attribute_row.attnum = any (constraint_row.conkey)
    where constraint_row.conrelid = 'public.shared_templates'::regclass
      and constraint_row.contype = 'c'
      and attribute_row.attname = 'apply_cost'
  loop
    execute format(
      'alter table public.shared_templates drop constraint %I',
      v_constraint_name
    );
  end loop;
end;
$$;

-- Every existing price is already within 1..3; only currently visible
-- marketplace listings are intentionally converted to free listings.
update public.shared_templates
set apply_cost = 0
where status = 'published';

alter table public.shared_templates
  add constraint shared_templates_apply_cost_range_check
  check (apply_cost between 0 and 3);

create or replace function public.reserve_shared_template(
  p_template_id uuid,
  p_source_reel_id uuid,
  p_title text,
  p_description text,
  p_apply_cost integer,
  p_tags text[]
)
returns table (
  template_id uuid,
  preview_path text,
  style_reference_path text,
  publish_template_limit integer,
  published_template_count integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_rights record;
  v_count integer := 0;
  v_reel public.creator_reels%rowtype;
  v_title text := trim(coalesce(p_title, ''));
  v_description text := trim(coalesce(p_description, ''));
  v_tags text[] := array(select distinct trim(value) from unnest(coalesce(p_tags, array[]::text[])) value order by trim(value));
  v_prefix text;
  v_allowed_tags constant text[] := array[
    '日常招呼', '可愛療癒', '搞笑吐槽', '心情情緒', '情侶曖昧',
    '親友互動', '職場同事', '校園學生', '家庭親子', '寵物日常',
    '節日祝福', '生日慶祝', '加油鼓勵', '感謝道歉', '美食生活',
    '旅行出遊', '運動健身', '商務客服', '自媒體創作', '其他主題'
  ];
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  if p_template_id is null or p_source_reel_id is null
     or char_length(v_title) not between 3 and 80 or char_length(v_description) > 300
     or p_apply_cost not between 0 and 3 or cardinality(v_tags) not between 1 and 3
     or exists (select 1 from unnest(v_tags) tag where not (tag = any(v_allowed_tags))) then
    raise exception 'invalid_shared_template' using errcode = '22023';
  end if;

  select * into v_rights from public.get_my_creator_reel_library_status();
  if not v_rights.marketplace_eligible or v_rights.publish_template_limit = 0 then
    raise exception 'shared_templates_marketplace_required' using errcode = '42501';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 47));
  select * into v_reel from public.creator_reels r
  where r.id = p_source_reel_id and r.owner_id = v_user and r.status = 'collected'
  for update;
  if not found then raise exception 'creator_reel_not_collected' using errcode = 'P0002'; end if;

  delete from public.shared_templates t where t.owner_id = v_user
    and t.status = 'uploading' and t.created_at < now() - interval '30 minutes';
  if exists (select 1 from public.shared_templates t where t.owner_id = v_user
    and t.source_reel_id = p_source_reel_id and t.status in ('uploading', 'published')) then
    raise exception 'shared_template_already_exists' using errcode = '23505';
  end if;
  select count(*)::integer into v_count from public.shared_templates t
  where t.owner_id = v_user and t.status in ('uploading', 'published');
  if v_count >= v_rights.publish_template_limit then
    raise exception 'shared_template_publish_limit' using errcode = '23514';
  end if;

  v_prefix := v_user::text || '/' || p_template_id::text || '/';
  insert into public.shared_templates (
    id, owner_id, source_reel_id, title, description, apply_cost, tags, status,
    snapshot, preview_path, style_reference_path
  ) values (
    p_template_id, v_user, v_reel.id, v_title, v_description, p_apply_cost, v_tags,
    'uploading', v_reel.snapshot, v_prefix || 'preview.webp',
    case when v_reel.style_reference_path is not null then v_prefix || 'style-reference.webp' else null end
  );
  return query select p_template_id, v_prefix || 'preview.webp',
    case when v_reel.style_reference_path is not null then v_prefix || 'style-reference.webp' else null end,
    v_rights.publish_template_limit, v_count + 1;
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
  v_rights record;
  v_template public.shared_templates%rowtype;
  v_existing public.shared_template_redemptions%rowtype;
  v_balance integer := 0;
  v_remaining integer := 0;
  v_grant record;
  v_charge integer := 0;
  v_shared_count integer := 0;
begin
  if v_user is null then raise exception 'not_authenticated' using errcode = '28000'; end if;
  if p_template_id is null or p_idempotency_key is null then
    raise exception 'invalid_shared_template_redemption' using errcode = '22023';
  end if;
  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 53));
  select * into v_existing from public.shared_template_redemptions r
  where r.consumer_id = v_user and r.template_id = p_template_id for update;
  if found then
    return query select v_existing.template_id,
      (select t.title from public.shared_templates t where t.id = v_existing.template_id),
      0, public.get_my_credit_balance_for_user(v_user), v_existing.snapshot,
      v_existing.style_reference_path, true;
    return;
  end if;
  select * into v_rights from public.get_my_creator_reel_library_status();
  if not v_rights.marketplace_eligible then
    raise exception 'shared_templates_marketplace_required' using errcode = '42501';
  end if;
  select * into v_template from public.shared_templates t
  where t.id = p_template_id and t.status = 'published' for update;
  if not found then raise exception 'shared_template_not_available' using errcode = 'P0002'; end if;
  if v_template.owner_id <> v_user then
    select count(*)::integer into v_shared_count from public.creator_reels r
    where r.owner_id = v_user and r.status = 'collected';
    select v_shared_count + count(*)::integer into v_shared_count
    from public.shared_template_redemptions redemption
    join public.shared_templates template on template.id = redemption.template_id
    where redemption.consumer_id = v_user and template.owner_id <> v_user;
    if v_shared_count >= v_rights.collected_reel_limit then
      raise exception 'shared_template_collection_limit' using errcode = '23514';
    end if;
    v_charge := v_template.apply_cost;
    v_balance := public.get_my_credit_balance_for_user(v_user);
    if v_balance < v_charge then raise exception 'shared_template_insufficient_credits' using errcode = '22023'; end if;
    v_remaining := v_charge;
    for v_grant in
      select g.id, g.remaining_credits from public.image_credit_grants g
      where g.user_id = v_user and g.starts_at <= now() and g.expires_at > now()
        and g.remaining_credits > 0
      order by case
          when g.source_kind in ('legacy', 'trial', 'admin') then 1
          when g.source_kind = 'referral' then 2
          when g.source_kind in ('subscription', 'credit_pack') then 3
          when g.source_kind = 'template_reward' then 4
          else 5
        end,
        g.expires_at asc, g.created_at asc
      for update
    loop
      exit when v_remaining = 0;
      update public.image_credit_grants g
      set remaining_credits = g.remaining_credits - least(g.remaining_credits, v_remaining)
      where g.id = v_grant.id;
      v_remaining := v_remaining - least(v_grant.remaining_credits, v_remaining);
    end loop;
    if v_charge > 0 then
      insert into public.image_credit_grants (
        user_id, total_credits, remaining_credits, starts_at, expires_at,
        external_reference, source_kind
      ) values (
        v_template.owner_id, v_charge, v_charge, now(), now() + interval '90 days',
        'template-reward:' || p_template_id::text || ':' || v_user::text, 'template_reward'
      );
    end if;
  end if;
  insert into public.shared_template_redemptions (
    template_id, consumer_id, idempotency_key, charged_cost, snapshot, style_reference_path
  ) values (
    v_template.id, v_user, p_idempotency_key, v_charge, v_template.snapshot, v_template.style_reference_path
  );
  return query select v_template.id, v_template.title, v_charge,
    public.get_my_credit_balance_for_user(v_user), v_template.snapshot,
    v_template.style_reference_path, false;
end;
$$;

revoke all on function public.reserve_shared_template(uuid, uuid, text, text, integer, text[]) from public, anon;
grant execute on function public.reserve_shared_template(uuid, uuid, text, text, integer, text[]) to authenticated;
revoke all on function public.redeem_shared_template(uuid, uuid) from public, anon;
grant execute on function public.redeem_shared_template(uuid, uuid) to authenticated;

-- Defensive compatibility for an older cached redemption function: a free
-- listing must never create an invalid zero-credit reward grant.
create or replace function public.skip_zero_template_reward()
returns trigger
language plpgsql
as $$
begin
  if new.source_kind = 'template_reward' and new.total_credits = 0 then
    return null;
  end if;
  return new;
end;
$$;

drop trigger if exists image_credit_grants_skip_zero_template_reward
  on public.image_credit_grants;
create trigger image_credit_grants_skip_zero_template_reward
before insert on public.image_credit_grants
for each row execute function public.skip_zero_template_reward();

notify pgrst, 'reload schema';
