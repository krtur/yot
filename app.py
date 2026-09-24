"""YT Prospector: interface web. Rode com:  streamlit run app.py"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import dotenv
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prospector import __version__, export, pipeline, supabase_sink  # noqa: E402
from prospector.youtube import (  # noqa: E402
    DEFAULT_SEARCH_CALLS_PER_DAY,
    DEFAULT_UNITS_PER_DAY,
    LocalStore,
    YouTubeAPIError,
    YouTubeClient,
)

st.set_page_config(page_title="YT Prospector", page_icon="🔎", layout="wide")
dotenv.load_dotenv(ROOT / ".env")


def _env_int(name: str, default: int) -> int:
    try:
        return max(int(os.getenv(name, "")), 1)
    except ValueError:
        return default


API_KEY = os.getenv("YOUTUBE_API_KEY", "").strip()
CACHE_PATH = os.getenv("YTP_CACHE") or str(ROOT / ".cache" / "youtube.sqlite")
SEARCH_LIMIT = _env_int("YTP_MAX_BUSCAS_DIA", DEFAULT_SEARCH_CALLS_PER_DAY)
UNITS_LIMIT = _env_int("YTP_MAX_UNIDADES_DIA", DEFAULT_UNITS_PER_DAY)
STAGES = list(pipeline.STAGE_LABELS)
FILE_TYPES = {
    "xlsx": ("Excel (.xlsx)", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "csv": ("CSV", "text/csv"),
    "json": ("JSON", "application/json"),
}
INT_COLUMNS = ("Inscritos", "Visualizações totais", "Média de visualizações", "Média de curtidas",
               "Média de comentários")
fmt = export.format_int


def lines(text: str | None) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


# ---------------------------------------------------------------------- filtros
with st.sidebar:
    st.header("Filtros")
    mode = st.radio(
        "Como buscar", pipeline.MODES, format_func=pipeline.MODE_LABELS.get, key="mode",
        help="Vídeos sobre o nicho encontra quem publica sobre o tema (recomendado). "
             "Canais pelo nome encontra canais cujo nome ou descrição citam o termo.",
    )
    pages = st.slider("Páginas por nicho", 1, pipeline.MAX_PAGES_PER_NICHE, 1, key="pages",
                      help="Cada página traz até 50 resultados e gasta 1 busca da cota diária.")
    region = st.text_input("Região", "BR", key="region", max_chars=2,
                           help="Código do país (BR, PT, US...). Deixe vazio para qualquer região.")
    language = st.text_input("Idioma", "pt", key="language", max_chars=7,
                             help="pt, en, es... Deixe vazio para qualquer idioma.")
    order = st.selectbox("Ordenar busca por", pipeline.ORDERS, format_func=pipeline.ORDER_LABELS.get, key="order")
    published_after = None
    if st.checkbox("Só publicações recentes", key="use_date"):
        published_after = st.date_input("Publicados após", value=date.today() - timedelta(days=365),
                                        key="published_after", format="DD/MM/YYYY")
    st.divider()
    min_subs = st.number_input("Mínimo de inscritos", min_value=0, value=0, step=1_000, key="min_subs",
                               help="0 = sem mínimo.")
    max_subs = st.number_input("Máximo de inscritos", min_value=0, value=0, step=10_000, key="max_subs",
                               help="0 = sem máximo.")
    active_days = st.number_input("Postou nos últimos (dias)", min_value=0, max_value=3650, value=0, step=30,
                                  key="active_days", help="0 = não filtrar por atividade.")
    max_channels = st.number_input("Máximo de canais", min_value=0, value=0, step=10, key="max_channels",
                                   help="0 = sem limite. Com limite, entram primeiro os canais que mais "
                                        "apareceram na busca.")
    recent = st.slider("Vídeos recentes por canal", 0, pipeline.MAX_RECENT_VIDEOS, 10, key="recent",
                       help="Base das médias e do filtro de atividade. Cada canal gasta 1 unidade para listar.")
    st.divider()
    st.subheader("Cota de hoje")
    store = LocalStore(CACHE_PATH)
    for bucket, label, limit in (("search", "Buscas", SEARCH_LIMIT), ("units", "Unidades", UNITS_LIMIT)):
        used = store.used(bucket)
        st.progress(min(used / limit, 1.0), text=f"{label}: {fmt(used)} de {fmt(limit)}")
    st.caption("Contagem desta máquina. Renova à meia-noite no horário do Pacífico (4h ou 5h em Brasília).")

# ------------------------------------------------------------------------ busca
st.title("YT Prospector")
st.caption("Encontre canais do YouTube por nicho e exporte os dados públicos de cada perfil, "
           "direto da API oficial.")

if not API_KEY:
    st.info("Para começar, crie uma chave da YouTube Data API v3 e coloque em YOUTUBE_API_KEY no arquivo .env. "
            "O passo a passo está no README.", icon="🔑")

left, right = st.columns(2)
niches_text = left.text_area("Nichos ou palavras-chave", key="niches", height=130,
                             placeholder="finanças pessoais\ninvestimentos para iniciantes", help="Um por linha.")
profiles_text = right.text_area("Perfis específicos (opcional)", key="profiles", height=130,
                                placeholder="@nomedocanal\nhttps://www.youtube.com/@outrocanal",
                                help="Um por linha: @handle, link do canal, link de um vídeo ou ID UC... "
                                     "Perfis informados entram no relatório mesmo fora dos filtros.")
extract = st.checkbox(
    "Coletar contatos publicados pelo criador", key="extract",
    help="Lê a descrição do canal e só os links repetidos em 2 ou mais vídeos recentes (links de um vídeo só "
         "costumam ser de patrocinadores). O e-mail da aba Sobre fica atrás de captcha: confira manualmente.",
)
only_contact = st.checkbox("Mostrar só canais com contato", key="only_contact", disabled=not extract) and extract

cfg = pipeline.SearchConfig(
    niches=lines(niches_text),
    profiles=lines(profiles_text),
    mode=mode,
    pages_per_niche=pages,
    region=region,
    language=language,
    order=order,
    published_after=published_after,
    min_subscribers=min_subs,
    max_subscribers=max_subs,
    active_within_days=active_days,
    recent_videos=recent,
    extract_contacts=extract,
    only_with_contact=only_contact,
    max_channels=max_channels,
)
if cfg.niches or cfg.profiles:
    estimate = cfg.estimate()
    st.caption(f"Teto desta busca: {fmt(estimate['search_calls'])} de {fmt(SEARCH_LIMIT)} buscas diárias e "
               f"{fmt(estimate['max_units'])} de {fmt(UNITS_LIMIT)} unidades. O consumo real costuma ser menor.")


def execute(config: pipeline.SearchConfig) -> None:
    bar = st.progress(0.0, text="Preparando...")

    def progress(stage: str, done: int, total: int) -> None:
        position = STAGES.index(stage) if stage in STAGES else 0
        share = done / total if total else 1.0
        bar.progress(min((position + share) / len(STAGES), 1.0),
                     text=f"{pipeline.STAGE_LABELS.get(stage, stage)} ({done}/{total})")

    try:
        client = YouTubeClient(API_KEY, cache_path=CACHE_PATH, search_calls_per_day=SEARCH_LIMIT,
                               units_per_day=UNITS_LIMIT)
        result = pipeline.run(client, config, progress)
    except YouTubeAPIError as exc:
        bar.empty()
        st.error(str(exc))
        return
    base = export.default_basename(result)
    st.session_state["result"] = result
    st.session_state["files"] = {
        "xlsx": (export.xlsx_bytes(result), f"{base}.xlsx"),
        "csv": (export.csv_bytes(result), f"{base}.csv"),
        "json": (export.json_bytes(result), f"{base}.json"),
    }
    st.rerun()


if st.button("Buscar canais", type="primary", key="run", disabled=not API_KEY):
    try:
        cfg.validate()
    except ValueError as exc:
        st.error(str(exc))
    else:
        execute(cfg)


# -------------------------------------------------------------------- resultado
def channel_column_config() -> dict[str, Any]:
    config: dict[str, Any] = {
        "Aba Sobre": st.column_config.LinkColumn(
            "Aba Sobre", display_text="Abrir",
            help="O e-mail comercial fica atrás de um captcha: confira ali e anote na planilha."),
        "URL do canal": st.column_config.LinkColumn("URL do canal"),
        "Engajamento (%)": st.column_config.NumberColumn(
            "Engajamento (%)", format="%.2f%%",
            help="(curtidas + comentários) ÷ visualizações nos vídeos analisados."),
        "Descrição do canal": st.column_config.TextColumn("Descrição do canal", width="large"),
    }
    for name in INT_COLUMNS:
        config[name] = st.column_config.NumberColumn(name, format="localized")
    return config


def show_result(result: pipeline.RunResult) -> None:
    stats = result.stats
    with_contacts = result.config.extract_contacts
    st.divider()
    first, second, third, fourth = st.columns(4)
    first.metric("Canais no relatório", fmt(stats["channels_in_report"]))
    second.metric("Encontrados na busca", fmt(stats["channels_discovered"]))
    if with_contacts:
        third.metric("Com e-mail publicado", fmt(stats["with_email"]))
        fourth.metric("Com contato direto", fmt(stats["with_direct_contact"]),
                      help="E-mail, WhatsApp ou telefone publicados pelo criador.")
    else:
        usage = stats["usage_this_run"]
        third.metric("Buscas usadas", fmt(usage.get("search", 0)))
        fourth.metric("Unidades usadas", fmt(usage.get("units", 0)))
    for warning in stats["warnings"]:
        st.warning(warning)
    if stats["unresolved_profiles"]:
        st.info("Perfis não encontrados: " + ", ".join(stats["unresolved_profiles"]))
    if not result.records:
        st.info("Nenhum canal passou pelos filtros. Tente afrouxar a faixa de inscritos ou o período de atividade.")

    labels = ["Canais", *(["Contatos"] if with_contacts else []), "Nichos", "Resumo"]
    tabs = dict(zip(labels, st.tabs(labels)))
    with tabs["Canais"]:
        st.dataframe(export.table_rows(result, pct_as_percent=True), width="stretch", hide_index=True,
                     column_config=channel_column_config())
    if with_contacts:
        with tabs["Contatos"]:
            st.caption(export.CONTACT_NOTE)
            st.dataframe(export.contact_table(result), width="stretch", hide_index=True,
                         column_config={"URL do canal": st.column_config.LinkColumn("URL do canal")})
    with tabs["Nichos"]:
        tables = export.niche_tables(result)
        names = list(tables)
        st.markdown(f"**{names[0]}**")
        st.dataframe(tables[names[0]], width="stretch", hide_index=True)
        for column, name in zip(st.columns(2), names[1:]):
            column.markdown(f"**{name}**")
            column.dataframe(tables[name], width="stretch", hide_index=True)
    with tabs["Resumo"]:
        sections: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for section, label, value in export.summary_items(result):
            sections[section].append((label, value))
        for section, items in sections.items():
            st.markdown(f"**{section}**")
            st.table(pd.DataFrame({"Detalhe": [value for _, value in items]},
                                  index=[label for label, _ in items]))

    st.markdown("**Baixar**")
    files = st.session_state.get("files", {})
    for column, kind in zip(st.columns(len(FILE_TYPES)), FILE_TYPES):
        if kind in files:
            data, name = files[kind]
            label, mime = FILE_TYPES[kind]
            column.download_button(label, data=data, file_name=name, mime=mime, key=f"download_{kind}",
                                   type="primary" if kind == "xlsx" else "secondary", width="stretch")

    url, key, table = supabase_sink.settings()
    if url and key and st.button(f"Enviar para o Supabase ({table})", key="supabase"):
        try:
            sent = supabase_sink.upsert(result, url, key, table)
        except supabase_sink.SupabaseError as exc:
            st.error(str(exc))
        else:
            st.success(f"{fmt(sent)} canais gravados na tabela {table}.")


result = st.session_state.get("result")
if result is not None:
    show_result(result)
elif API_KEY:
    st.caption("Informe nichos ou perfis e clique em Buscar canais.")

st.divider()
st.caption(f"{export.SOURCE_NOTE} {export.RETENTION_NOTE} YT Prospector {__version__}.")
