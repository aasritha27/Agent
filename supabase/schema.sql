-- University Room Scheduler — Supabase schema
-- Run this once in the Supabase SQL editor (Dashboard → SQL → New query).

-- roles: 'coordinator' (generate/approve) or 'viewer' (faculty)
create table if not exists profiles (
  id uuid primary key references auth.users on delete cascade,
  role text not null default 'viewer' check (role in ('coordinator', 'viewer')),
  full_name text
);

create table if not exists faculty (
  id text primary key,
  name text not null,
  max_per_day int not null default 4
);

create table if not exists rooms (
  id text primary key,
  name text not null,
  capacity int not null,
  type text not null check (type in ('Class', 'Lab'))
);

create table if not exists sections (
  id text primary key,
  name text not null,
  students int not null
);

create table if not exists subjects (
  id text primary key,
  code text not null,
  name text not null
);

create table if not exists section_subjects (
  section_id text not null references sections(id) on delete cascade,
  subject_id text not null references subjects(id) on delete cascade,
  faculty_id text not null references faculty(id),
  lecture_hours int not null default 3,
  practical_hours int not null default 2,
  primary key (section_id, subject_id)
);

create table if not exists schedules (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  created_by uuid references auth.users,
  status text not null default 'active' check (status in ('active', 'archived')),
  sessions jsonb not null,
  stats jsonb
);

create table if not exists room_requests (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users,
  section_id text references sections(id),
  preferred_room text,
  reason text not null,
  letter_path text,
  status text not null default 'pending'
    check (status in ('pending', 'approved', 'rejected')),
  created_at timestamptz not null default now(),
  decided_by uuid references auth.users,
  decided_at timestamptz
);

create index if not exists schedules_status_idx on schedules (status, created_at desc);
create index if not exists room_requests_status_idx on room_requests (status, created_at desc);

-- storage bucket for request letters
insert into storage.buckets (id, name, public)
values ('letters', 'letters', false)
on conflict (id) do nothing;

-- ---- row level security ----------------------------------------------------
-- The backend talks to Supabase with the service role key (bypasses RLS);
-- these policies cover direct browser access with the anon key.

alter table profiles enable row level security;
create policy "read own profile" on profiles
  for select using (auth.uid() = id);

alter table faculty enable row level security;
create policy "authenticated read" on faculty for select using (auth.role() = 'authenticated');
alter table rooms enable row level security;
create policy "authenticated read" on rooms for select using (auth.role() = 'authenticated');
alter table sections enable row level security;
create policy "authenticated read" on sections for select using (auth.role() = 'authenticated');
alter table subjects enable row level security;
create policy "authenticated read" on subjects for select using (auth.role() = 'authenticated');
alter table section_subjects enable row level security;
create policy "authenticated read" on section_subjects for select using (auth.role() = 'authenticated');
alter table schedules enable row level security;
create policy "authenticated read" on schedules for select using (auth.role() = 'authenticated');

alter table room_requests enable row level security;
create policy "insert own" on room_requests
  for insert with check (auth.uid() = user_id);
create policy "read own or coordinator" on room_requests
  for select using (
    auth.uid() = user_id
    or exists (select 1 from profiles p where p.id = auth.uid() and p.role = 'coordinator')
  );

-- auto-create a profile row on signup
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer as $$
begin
  insert into public.profiles (id, full_name)
  values (new.id, coalesce(new.raw_user_meta_data->>'full_name', new.email))
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
