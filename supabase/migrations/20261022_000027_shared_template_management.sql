-- Creator-facing shared-template management.  Publishers can temporarily
-- withdraw or restore their own templates; users who unlocked a template
-- retain their private reapply right even after it leaves the marketplace.

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
        t.style_reference_path = p_name
        and exists (
          select 1 from public.shared_template_redemptions r
          where r.template_id = t.id and r.consumer_id = auth.uid()
        )
      )
    ) and (t.preview_path = p_name or t.style_reference_path = p_name)
  );
$$;

create or replace function public.get_my_shared_template_management()
returns table (
  template_id uuid,
  source_reel_id uuid,
  title text,
  description text,
  apply_cost integer,
  status text,
  published_at timestamptz,
  updated_at timestamptz,
  unlock_count bigint,
  reward_credits bigint
)
language sql
stable
security definer
set search_path = public
as $$
  select
    t.id,
    t.source_reel_id,
    t.title,
    t.description,
    t.apply_cost,
    t.status,
    t.published_at,
    t.updated_at,
    count(r.id) filter (where r.consumer_id <> t.owner_id),
    coalesce(sum(r.charged_cost) filter (where r.consumer_id <> t.owner_id), 0)
  from public.shared_templates t
  left join public.shared_template_redemptions r on r.template_id = t.id
  where t.owner_id = auth.uid()
  group by t.id
  order by case when t.status = 'published' then 0 else 1 end,
    t.updated_at desc;
$$;

create or replace function public.set_my_shared_template_visibility(
  p_template_id uuid,
  p_publish boolean
)
returns table (template_id uuid, status text)
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

  update public.shared_templates t
  set
    status = case when p_publish then 'published' else 'withdrawn' end,
    published_at = case when p_publish then now() else t.published_at end
  where t.id = p_template_id
    and t.owner_id = v_user
    and t.status in ('published', 'withdrawn')
  returning t.id, t.status into template_id, status;

  if not found then
    raise exception 'shared_template_not_owned' using errcode = 'P0002';
  end if;
  return next;
end;
$$;

revoke all on function public.shared_template_asset_is_readable(text) from public, anon;
revoke all on function public.get_my_shared_template_management() from public, anon;
revoke all on function public.set_my_shared_template_visibility(uuid, boolean) from public, anon;
grant execute on function public.shared_template_asset_is_readable(text) to authenticated;
grant execute on function public.get_my_shared_template_management() to authenticated;
grant execute on function public.set_my_shared_template_visibility(uuid, boolean) to authenticated;

notify pgrst, 'reload schema';
