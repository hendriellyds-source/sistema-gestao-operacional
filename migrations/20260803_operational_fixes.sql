-- CORREÇÕES OPERACIONAIS — fotos de perfis e saneamento seletivo de demo.
-- A migração é aditiva e idempotente. Registros manuais de calendário ligados
-- aos identificadores fictícios conhecidos são apenas excluídos logicamente.

alter table public.profiles
    add column if not exists avatar_bucket text,
    add column if not exists avatar_path text,
    add column if not exists avatar_mime_type text,
    add column if not exists avatar_updated_at timestamptz;

do $migration$
begin
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.profiles'::regclass
          and conname = 'profiles_avatar_bucket_ck'
    ) then
        alter table public.profiles
            add constraint profiles_avatar_bucket_ck
            check (
                (avatar_path is null and avatar_bucket is null)
                or (avatar_path is not null and avatar_bucket = 'user-profile-photos')
            );
    end if;
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.profiles'::regclass
          and conname = 'profiles_avatar_mime_ck'
    ) then
        alter table public.profiles
            add constraint profiles_avatar_mime_ck
            check (
                avatar_mime_type is null
                or avatar_mime_type in ('image/jpeg', 'image/png')
            );
    end if;
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.profiles'::regclass
          and conname = 'profiles_avatar_path_ck'
    ) then
        alter table public.profiles
            add constraint profiles_avatar_path_ck
            check (
                avatar_path is null
                or avatar_path ~ (
                    '^' || organization_id::text || '/profiles/' || id::text
                    || '/[A-Za-z0-9_-]+[.](jpg|jpeg|png)$'
                )
            );
    end if;
end
$migration$;

-- O bucket user-profile-photos deve ser criado manualmente como privado.
-- A migração não altera storage.buckets e, portanto, não muda buckets atuais.
drop policy if exists profile_photos_select_same_organization on storage.objects;
create policy profile_photos_select_same_organization
on storage.objects for select to authenticated
using (
    bucket_id = 'user-profile-photos'
    and exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.status = 'active'
          and p.organization_id::text = split_part(name, '/', 1)
    )
);

drop policy if exists profile_photos_insert_admin on storage.objects;
create policy profile_photos_insert_admin
on storage.objects for insert to authenticated
with check (
    bucket_id = 'user-profile-photos'
    and exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.status = 'active'
          and p.role_code in ('admin', 'director')
          and p.organization_id::text = split_part(name, '/', 1)
    )
);

drop policy if exists profile_photos_update_admin on storage.objects;
create policy profile_photos_update_admin
on storage.objects for update to authenticated
using (
    bucket_id = 'user-profile-photos'
    and exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.status = 'active'
          and p.role_code in ('admin', 'director')
          and p.organization_id::text = split_part(name, '/', 1)
    )
)
with check (
    bucket_id = 'user-profile-photos'
    and exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.status = 'active'
          and p.role_code in ('admin', 'director')
          and p.organization_id::text = split_part(name, '/', 1)
    )
);

drop policy if exists profile_photos_delete_admin on storage.objects;
create policy profile_photos_delete_admin
on storage.objects for delete to authenticated
using (
    bucket_id = 'user-profile-photos'
    and exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.status = 'active'
          and p.role_code in ('admin', 'director')
          and p.organization_id::text = split_part(name, '/', 1)
    )
);

update public.financial_calendar_entries
set deleted_at = coalesce(deleted_at, now()),
    updated_at = now()
where deleted_at is null
  and (
      upper(regexp_replace(coalesce(vehicle_id, ''), '[^A-Z0-9]', '', 'g'))
          in ('ABC1D23', 'DEF4G56', 'GHI7J89')
      or lower(coalesce(title, '')) similar to
          '%(veículo|veiculo)[[:space:]]+(demo|teste|de teste)%'
  );
