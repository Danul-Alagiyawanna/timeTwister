-- TimeTwister bronze layer (run once in Supabase SQL Editor or via supabase db push)

create table if not exists outlets (
  id text primary key,
  display_name text not null,
  language text not null check (language in ('English', 'Sinhala', 'Tamil')),
  homepage_url text,
  is_active boolean not null default true
);

create table if not exists pipeline_runs (
  id uuid primary key default gen_random_uuid(),
  run_type text not null default 'scrape',
  status text not null default 'running',
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  articles_in int not null default 0,
  articles_out int not null default 0,
  error_message text,
  github_run_id text,
  metadata jsonb not null default '{}'::jsonb
);

create table if not exists raw_articles (
  id uuid primary key default gen_random_uuid(),
  url text not null unique,
  outlet_id text not null references outlets (id),
  title text not null,
  summary text,
  description text,
  published_at timestamptz,
  image_url text,
  date_source text,
  scraped_at timestamptz not null default now(),
  scrape_run_id uuid references pipeline_runs (id),
  raw_payload jsonb,
  content_hash text
);

create index if not exists idx_raw_articles_scraped on raw_articles (scraped_at desc);
create index if not exists idx_raw_articles_outlet on raw_articles (outlet_id, scraped_at desc);

-- Seed outlets (ids match scraper_registry.py)
insert into outlets (id, display_name, language, homepage_url) values
  ('sundaytimes', 'Sunday Times', 'English', 'https://www.sundaytimes.lk'),
  ('dailynews', 'Daily News', 'English', 'https://dailynews.lk'),
  ('ceylontoday', 'Ceylon Today', 'English', 'https://ceylontoday.lk'),
  ('dailymirror', 'Daily Mirror', 'English', 'https://www.dailymirror.lk'),
  ('ftlk', 'FT.lk', 'English', 'https://www.ft.lk'),
  ('economynext', 'Economy Next', 'English', 'https://economynext.com'),
  ('morning', 'The Morning', 'English', 'https://www.themorning.lk'),
  ('sundayobserver', 'Sunday Observer', 'English', 'https://www.sundayobserver.lk'),
  ('divaina', 'Divaina', 'Sinhala', 'https://www.divaina.lk'),
  ('lankadeepa', 'Lankadeepa', 'Sinhala', 'https://www.lankadeepa.lk'),
  ('aruna', 'Aruna', 'Sinhala', 'https://www.aruna.lk'),
  ('mawbima', 'Mawbima', 'Sinhala', 'https://mawbima.lk'),
  ('virakesari', 'Virakesari', 'Tamil', 'https://www.virakesari.lk'),
  ('thinakaran', 'Thinakaran', 'Tamil', 'https://www.thinakaran.lk'),
  ('thamilan', 'Thamilan', 'Tamil', 'https://www.thamilan.lk'),
  ('island', 'The Island', 'English', 'https://island.lk'),
  ('dinamina', 'Dinamina', 'Sinhala', 'https://www.dinamina.lk')
on conflict (id) do nothing;

alter table outlets enable row level security;
alter table pipeline_runs enable row level security;
alter table raw_articles enable row level security;
