-- A listing that has reached the three-member report threshold and is then
-- withdrawn by an administrator is permanently locked from publication.
-- The enforcement lives in triggers so every existing publish/review RPC is
-- protected without changing its public return signature.

alter table public.shared_templates
  add column if not exists moderation_republish_blocked boolean not null default false,
  add column if not exists moderation_republish_blocked_at timestamptz;

create or replace function public.lock_moderated_shared_template_republication()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if new.review_status = 'reviewed'
     and new.resolution = 'withdraw_listing'
     and (
       old.review_status is distinct from new.review_status
       or old.resolution is distinct from new.resolution
     ) then
    update public.shared_templates as template
    set
      status = 'withdrawn',
      moderation_republish_blocked = true,
      moderation_republish_blocked_at = now()
    where template.id = new.template_id;
  end if;
  return new;
end;
$$;

drop trigger if exists shared_template_moderation_queue_lock_listing
  on public.shared_template_moderation_queue;
create trigger shared_template_moderation_queue_lock_listing
after update of review_status, resolution
on public.shared_template_moderation_queue
for each row execute function public.lock_moderated_shared_template_republication();

create or replace function public.prevent_locked_shared_template_republication()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  if old.moderation_republish_blocked
     and not new.moderation_republish_blocked then
    raise exception 'shared_template_republication_blocked' using errcode = '42501';
  end if;

  if old.moderation_republish_blocked
     and new.status = 'published'
     and old.status is distinct from 'published' then
    raise exception 'shared_template_republication_blocked' using errcode = '42501';
  end if;
  return new;
end;
$$;

drop trigger if exists shared_templates_prevent_locked_republication
  on public.shared_templates;
create trigger shared_templates_prevent_locked_republication
before update on public.shared_templates
for each row execute function public.prevent_locked_shared_template_republication();

create or replace function public.get_my_shared_template_moderation_status()
returns table (
  template_id uuid,
  republish_blocked boolean,
  report_count integer,
  reviewed_at timestamptz,
  review_note text
)
language sql
stable
security definer
set search_path = public
as $$
  select
    template.id,
    template.moderation_republish_blocked,
    coalesce(queue.report_count, 0),
    queue.reviewed_at,
    queue.review_note
  from public.shared_templates as template
  left join public.shared_template_moderation_queue as queue
    on queue.template_id = template.id
  where template.owner_id = auth.uid()
    and template.status in ('published', 'withdrawn');
$$;

revoke all on function public.lock_moderated_shared_template_republication() from public, anon;
revoke all on function public.prevent_locked_shared_template_republication() from public, anon;
revoke all on function public.get_my_shared_template_moderation_status() from public, anon;
grant execute on function public.get_my_shared_template_moderation_status() to authenticated;

notify pgrst, 'reload schema';
