-- YT Prospector: tabela de canais no Supabase.
-- Rode no SQL Editor do projeto. Pode rodar de novo sem perder dados.
--
-- A ferramenta faz upsert por channel_id e só envia os dados vindos da API.
-- As colunas de trabalho da equipe (verified_business_email, status, assignee, notes)
-- nunca são enviadas por ela, então o que a equipe anotar aqui é preservado.

create table if not exists public.yt_channels (
  channel_id               text primary key,
  title                    text not null,
  handle                   text,
  url                      text not null,
  about_url                text,
  subscribers              bigint,          -- arredondado pela própria API; nulo quando o canal oculta
  subscribers_hidden       boolean not null default false,
  total_views              bigint,
  video_count              integer,
  country                  text,
  default_language         text,
  channel_created_at       timestamptz,
  topics                   text[] not null default '{}',   -- categorias atribuídas pelo YouTube
  keywords                 text[] not null default '{}',   -- palavras-chave declaradas pelo criador
  found_by                 text[] not null default '{}',   -- nichos em que o canal apareceu
  search_hits              integer not null default 0,
  source                   text,
  description              text,
  recent_videos_analyzed   integer not null default 0,
  avg_views_recent         numeric,
  avg_likes_recent         numeric,
  avg_comments_recent      numeric,
  engagement_rate_recent   numeric,         -- fração: 0.058 = 5,8%
  last_upload_at           timestamptz,
  days_since_last_upload   integer,
  avg_days_between_uploads numeric,
  recent_videos            jsonb not null default '[]'::jsonb,

  -- contatos publicados pelo criador (preenchidos só quando a coleta de contatos está ligada)
  emails                   text[] not null default '{}',
  whatsapp                 text[] not null default '{}',
  phones                   text[] not null default '{}',
  instagram                text[] not null default '{}',
  tiktok                   text[] not null default '{}',
  twitter_x                text[] not null default '{}',
  facebook                 text[] not null default '{}',
  linkedin                 text[] not null default '{}',
  kwai                     text[] not null default '{}',
  threads                  text[] not null default '{}',
  telegram                 text[] not null default '{}',
  discord                  text[] not null default '{}',
  twitch                   text[] not null default '{}',
  link_in_bio              text[] not null default '{}',
  websites                 text[] not null default '{}',
  primary_email            text,
  has_direct_contact       boolean not null default false,
  contact_sources          jsonb not null default '[]'::jsonb,

  -- trabalho da equipe (a ferramenta nunca escreve aqui)
  verified_business_email  text,
  status                   text not null default 'Novo'
                           check (status in ('Novo', 'Contatado', 'Respondeu', 'Em negociação', 'Fechado', 'Descartado')),
  assignee                 text,
  notes                    text,

  collected_at             timestamptz not null,           -- última coleta na API (base da retenção)
  first_seen_at            timestamptz not null default now()
);

create index if not exists yt_channels_subscribers_idx on public.yt_channels (subscribers desc nulls last);
create index if not exists yt_channels_status_idx on public.yt_channels (status);
create index if not exists yt_channels_collected_at_idx on public.yt_channels (collected_at);
create index if not exists yt_channels_found_by_idx on public.yt_channels using gin (found_by);

-- RLS ligado e sem políticas: só a secret key (ou a service_role legada) lê e grava.
-- Nunca use a publishable/anon key na ferramenta.
alter table public.yt_channels enable row level security;

-- Exemplo, se quiser liberar leitura para usuários logados do seu app:
-- create policy "equipe le canais" on public.yt_channels
--   for select to authenticated using (true);

-- Retenção de 30 dias (Políticas para Desenvolvedores do YouTube): dados da API precisam ser
-- atualizados ou apagados em até 30 dias. Rodar a ferramenta de novo atualiza collected_at.
-- Para apagar automaticamente o que não foi atualizado, ative a extensão pg_cron
-- (Database > Extensions) e rode uma vez:
-- select cron.schedule(
--   'yt-channels-retencao',
--   '0 9 * * *',   -- todo dia às 9h UTC (6h em Brasília)
--   $$delete from public.yt_channels where collected_at < now() - interval '30 days'$$
-- );
