-- Marketplace templates may cost only 1, 2, or 3 generation credits.
-- The application uses a select box, while this RPC rule prevents bypassing
-- that limit through a direct API request.

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
     or p_apply_cost not between 1 and 3 or cardinality(v_tags) not between 1 and 3
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

revoke all on function public.reserve_shared_template(uuid, uuid, text, text, integer, text[]) from public, anon;
grant execute on function public.reserve_shared_template(uuid, uuid, text, text, integer, text[]) to authenticated;

notify pgrst, 'reload schema';
