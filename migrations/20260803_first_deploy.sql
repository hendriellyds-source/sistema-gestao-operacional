-- PRIMEIRO DEPLOY — certificados de funcionários e calendário financeiro/RH.
-- Aplique manualmente no SQL Editor do Supabase somente após executar os backups
-- descritos em docs/PRIMEIRO_DEPLOY_RH_FINANCEIRO.md.

create extension if not exists pgcrypto;

create table if not exists public.employee_certificates (
    id uuid primary key default gen_random_uuid(),
    organization_id uuid not null references public.organizations(id) on delete restrict,
    employee_id bigint not null
        constraint employee_certificates_employee_id_ck check (employee_id > 0),
    course_name text not null check (char_length(course_name) between 1 and 240),
    issuing_institution text,
    issue_date date,
    expiration_date date,
    no_expiration boolean not null default false,
    certificate_number text,
    notes text,
    storage_bucket text not null default 'employee-certificates'
        constraint employee_certificates_storage_bucket_ck
        check (storage_bucket = 'employee-certificates'),
    storage_path text not null unique,
    original_filename text not null,
    mime_type text not null check (mime_type in ('application/pdf', 'image/jpeg', 'image/png')),
    file_size bigint not null check (file_size > 0 and file_size <= 10485760),
    registered_by_user_id uuid not null references auth.users(id) on delete restrict,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz,
    deleted_by_user_id uuid references auth.users(id) on delete restrict,
    constraint employee_certificates_validity_ck check (
        (no_expiration = true and expiration_date is null)
        or (no_expiration = false and expiration_date is not null)
    ),
    constraint employee_certificates_dates_ck check (
        issue_date is null or expiration_date is null or expiration_date >= issue_date
    ),
    constraint employee_certificates_storage_path_ck check (
        storage_path ~ (
            '^' || organization_id::text || '/employees/' || employee_id::text
            || '/[A-Za-z0-9_-]+[.](pdf|jpg|jpeg|png)$'
        )
    )
);

create index if not exists employee_certificates_employee_idx
    on public.employee_certificates (organization_id, employee_id, deleted_at);
create index if not exists employee_certificates_expiration_idx
    on public.employee_certificates (organization_id, expiration_date)
    where deleted_at is null and no_expiration = false;

create table if not exists public.financial_calendar_entries (
    id uuid primary key default gen_random_uuid(),
    organization_id uuid not null references public.organizations(id) on delete restrict,
    title text not null check (char_length(title) between 1 and 240),
    category text not null check (
        category in ('vehicle', 'employee', 'tax', 'supplier', 'payroll', 'document', 'maintenance', 'other')
    ),
    due_date date not null,
    amount numeric(14,2) check (amount is null or amount >= 0),
    supplier text,
    payment_method text,
    notes text,
    status text not null default 'pending' check (
        status in ('pending', 'scheduled', 'paid', 'overdue', 'cancelled')
    ),
    employee_id bigint
        constraint financial_calendar_employee_id_ck
        check (employee_id is null or employee_id > 0),
    vehicle_id text,
    recurrence_group_id uuid,
    recurrence_sequence integer check (recurrence_sequence is null or recurrence_sequence between 1 and 36),
    recurrence_frequency text check (
        recurrence_frequency is null or recurrence_frequency in ('weekly', 'monthly', 'yearly')
    ),
    recurrence_interval integer check (recurrence_interval is null or recurrence_interval between 1 and 12),
    recurrence_count integer check (recurrence_count is null or recurrence_count between 2 and 36),
    attachment_bucket text
        constraint financial_calendar_attachment_bucket_ck check (
        attachment_bucket is null
        or attachment_bucket = 'financial-calendar-attachments'
    ),
    attachment_path text unique,
    attachment_original_filename text,
    attachment_mime_type text check (
        attachment_mime_type is null or attachment_mime_type in ('application/pdf', 'image/jpeg', 'image/png')
    ),
    attachment_size bigint check (
        attachment_size is null or (attachment_size > 0 and attachment_size <= 10485760)
    ),
    created_by_user_id uuid not null references auth.users(id) on delete restrict,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    paid_at timestamptz,
    cancelled_at timestamptz,
    deleted_at timestamptz,
    deleted_by_user_id uuid references auth.users(id) on delete restrict,
    constraint financial_calendar_attachment_fields_ck check (
        (
            attachment_path is null
            and attachment_bucket is null
            and attachment_original_filename is null
            and attachment_mime_type is null
            and attachment_size is null
        )
        or (
            attachment_path is not null
            and attachment_bucket = 'financial-calendar-attachments'
            and attachment_original_filename is not null
            and attachment_mime_type is not null
            and attachment_size is not null
        )
    ),
    constraint financial_calendar_attachment_path_ck check (
        attachment_path is null
        or attachment_path ~ (
            '^' || organization_id::text || '/entries/' || id::text
            || '/[A-Za-z0-9_-]+[.](pdf|jpg|jpeg|png)$'
        )
    )
);

-- Permite reaplicar esta revisão sobre uma execução anterior do mesmo primeiro deploy.
do $migration$
begin
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.employee_certificates'::regclass
          and conname = 'employee_certificates_employee_id_ck'
    ) then
        alter table public.employee_certificates
            add constraint employee_certificates_employee_id_ck
            check (employee_id > 0);
    end if;
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.employee_certificates'::regclass
          and conname = 'employee_certificates_storage_bucket_ck'
    ) then
        alter table public.employee_certificates
            add constraint employee_certificates_storage_bucket_ck
            check (storage_bucket = 'employee-certificates');
    end if;
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.employee_certificates'::regclass
          and conname = 'employee_certificates_storage_path_ck'
    ) then
        alter table public.employee_certificates
            add constraint employee_certificates_storage_path_ck
            check (
                storage_path ~ (
                    '^' || organization_id::text || '/employees/' || employee_id::text
                    || '/[A-Za-z0-9_-]+[.](pdf|jpg|jpeg|png)$'
                )
            );
    end if;
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.financial_calendar_entries'::regclass
          and conname = 'financial_calendar_employee_id_ck'
    ) then
        alter table public.financial_calendar_entries
            add constraint financial_calendar_employee_id_ck
            check (employee_id is null or employee_id > 0);
    end if;
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.financial_calendar_entries'::regclass
          and conname = 'financial_calendar_attachment_bucket_ck'
    ) then
        alter table public.financial_calendar_entries
            add constraint financial_calendar_attachment_bucket_ck
            check (
                attachment_bucket is null
                or attachment_bucket = 'financial-calendar-attachments'
            );
    end if;
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.financial_calendar_entries'::regclass
          and conname = 'financial_calendar_attachment_fields_ck'
    ) then
        alter table public.financial_calendar_entries
            add constraint financial_calendar_attachment_fields_ck
            check (
                (
                    attachment_path is null
                    and attachment_bucket is null
                    and attachment_original_filename is null
                    and attachment_mime_type is null
                    and attachment_size is null
                )
                or (
                    attachment_path is not null
                    and attachment_bucket = 'financial-calendar-attachments'
                    and attachment_original_filename is not null
                    and attachment_mime_type is not null
                    and attachment_size is not null
                )
            );
    end if;
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.financial_calendar_entries'::regclass
          and conname = 'financial_calendar_attachment_path_ck'
    ) then
        alter table public.financial_calendar_entries
            add constraint financial_calendar_attachment_path_ck
            check (
                attachment_path is null
                or attachment_path ~ (
                    '^' || organization_id::text || '/entries/' || id::text
                    || '/[A-Za-z0-9_-]+[.](pdf|jpg|jpeg|png)$'
                )
            );
    end if;
end
$migration$;

create index if not exists financial_calendar_due_idx
    on public.financial_calendar_entries (organization_id, due_date, category, status)
    where deleted_at is null;
create index if not exists financial_calendar_recurrence_idx
    on public.financial_calendar_entries (recurrence_group_id, recurrence_sequence)
    where recurrence_group_id is not null;

alter table public.employee_certificates enable row level security;
alter table public.financial_calendar_entries enable row level security;

drop policy if exists employee_certificates_select_authorized on public.employee_certificates;
create policy employee_certificates_select_authorized
on public.employee_certificates for select to authenticated
using (
    exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.organization_id = employee_certificates.organization_id
          and p.status = 'active'
          and p.role_code in ('admin', 'director', 'financial')
    )
);

drop policy if exists employee_certificates_insert_authorized on public.employee_certificates;
create policy employee_certificates_insert_authorized
on public.employee_certificates for insert to authenticated
with check (
    registered_by_user_id = auth.uid()
    and exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.organization_id = employee_certificates.organization_id
          and p.status = 'active'
          and p.role_code in ('admin', 'director', 'financial')
    )
);

drop policy if exists employee_certificates_update_authorized on public.employee_certificates;
create policy employee_certificates_update_authorized
on public.employee_certificates for update to authenticated
using (
    exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.organization_id = employee_certificates.organization_id
          and p.status = 'active'
          and p.role_code in ('admin', 'director', 'financial')
    )
)
with check (
    exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.organization_id = employee_certificates.organization_id
          and p.status = 'active'
          and p.role_code in ('admin', 'director', 'financial')
    )
);

drop policy if exists financial_calendar_select_authorized on public.financial_calendar_entries;
create policy financial_calendar_select_authorized
on public.financial_calendar_entries for select to authenticated
using (
    exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.organization_id = financial_calendar_entries.organization_id
          and p.status = 'active'
          and p.role_code in ('admin', 'director', 'financial')
    )
);

drop policy if exists financial_calendar_insert_authorized on public.financial_calendar_entries;
create policy financial_calendar_insert_authorized
on public.financial_calendar_entries for insert to authenticated
with check (
    created_by_user_id = auth.uid()
    and exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.organization_id = financial_calendar_entries.organization_id
          and p.status = 'active'
          and p.role_code in ('admin', 'director', 'financial')
    )
);

drop policy if exists financial_calendar_update_authorized on public.financial_calendar_entries;
create policy financial_calendar_update_authorized
on public.financial_calendar_entries for update to authenticated
using (
    exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.organization_id = financial_calendar_entries.organization_id
          and p.status = 'active'
          and p.role_code in ('admin', 'director', 'financial')
    )
)
with check (
    exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.organization_id = financial_calendar_entries.organization_id
          and p.status = 'active'
          and p.role_code in ('admin', 'director', 'financial')
    )
);

-- Os buckets employee-certificates e financial-calendar-attachments devem ser
-- criados manualmente no Supabase como privados antes do uso da aplicação.
-- Esta migração não insere, atualiza nem exclui registros de storage.buckets.

drop policy if exists private_hr_finance_objects_select on storage.objects;
create policy private_hr_finance_objects_select
on storage.objects for select to authenticated
using (
    bucket_id in ('employee-certificates', 'financial-calendar-attachments')
    and exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.status = 'active'
          and p.role_code in ('admin', 'director', 'financial')
          and p.organization_id::text = split_part(name, '/', 1)
    )
);

drop policy if exists private_hr_finance_objects_insert on storage.objects;
create policy private_hr_finance_objects_insert
on storage.objects for insert to authenticated
with check (
    bucket_id in ('employee-certificates', 'financial-calendar-attachments')
    and exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.status = 'active'
          and p.role_code in ('admin', 'director', 'financial')
          and p.organization_id::text = split_part(name, '/', 1)
    )
);

drop policy if exists private_hr_finance_objects_update on storage.objects;
create policy private_hr_finance_objects_update
on storage.objects for update to authenticated
using (
    bucket_id in ('employee-certificates', 'financial-calendar-attachments')
    and exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.status = 'active'
          and p.role_code in ('admin', 'director', 'financial')
          and p.organization_id::text = split_part(name, '/', 1)
    )
)
with check (
    bucket_id in ('employee-certificates', 'financial-calendar-attachments')
    and exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.status = 'active'
          and p.role_code in ('admin', 'director', 'financial')
          and p.organization_id::text = split_part(name, '/', 1)
    )
);

drop policy if exists private_hr_finance_objects_delete on storage.objects;
create policy private_hr_finance_objects_delete
on storage.objects for delete to authenticated
using (
    bucket_id in ('employee-certificates', 'financial-calendar-attachments')
    and exists (
        select 1 from public.profiles p
        where p.auth_user_id = auth.uid()
          and p.status = 'active'
          and p.role_code in ('admin', 'director', 'financial')
          and p.organization_id::text = split_part(name, '/', 1)
    )
);
