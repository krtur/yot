"""Envio opcional dos canais para uma tabela do Supabase, via API REST (PostgREST).

Faz upsert por channel_id: canais novos entram, canais já gravados têm os dados da API atualizados.
As colunas de trabalho da equipe (status, responsável, observações, e-mail verificado) nunca são
enviadas, então o que a equipe anotou no Supabase é preservado. Estrutura da tabela em
sql/supabase_schema.sql.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from typing import Any, Mapping

import requests

from .contacts import CONTACT_FIELDS
from .pipeline import RunResult
from .youtube import chunks

DEFAULT_TABLE = "yt_channels"
BATCH_SIZE = 500

BASE_KEYS = (
    "channel_id", "title", "handle", "url", "about_url",
    "subscribers", "subscribers_hidden", "total_views", "video_count",
    "country", "default_language", "channel_created_at",
    "topics", "keywords", "found_by", "search_hits", "source", "description",
    "recent_videos_analyzed", "avg_views_recent", "avg_likes_recent", "avg_comments_recent",
    "engagement_rate_recent", "last_upload_at", "days_since_last_upload", "avg_days_between_uploads",
    "recent_videos", "collected_at",
)
CONTACT_KEYS = (*CONTACT_FIELDS, "primary_email", "has_direct_contact", "contact_sources")
_LIST_KEYS = {"topics", "keywords", "found_by", "recent_videos", "contact_sources", *CONTACT_FIELDS}
_NULL_IF_EMPTY = {"handle", "country", "default_language", "description", "primary_email"}
_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class SupabaseError(RuntimeError):
    """Falha ao gravar no Supabase, com mensagem pronta para mostrar ao usuário."""


def settings(environ: Mapping[str, str] | None = None) -> tuple[str, str, str]:
    """(url, chave, tabela) lidos de SUPABASE_URL, SUPABASE_SECRET_KEY e SUPABASE_TABLE."""
    env = os.environ if environ is None else environ
    return (
        env.get("SUPABASE_URL", "").strip(),
        env.get("SUPABASE_SECRET_KEY", "").strip(),
        env.get("SUPABASE_TABLE", "").strip() or DEFAULT_TABLE,
    )


def is_configured(environ: Mapping[str, str] | None = None) -> bool:
    url, key, _ = settings(environ)
    return bool(url and key)


def to_row(record: dict[str, Any], with_contacts: bool = True) -> dict[str, Any]:
    """Linha da tabela: sempre as mesmas chaves (exigência do insert em lote do PostgREST)."""
    row = {key: record.get(key) for key in BASE_KEYS}
    if with_contacts:
        found = record.get("contacts") or {}
        for field in CONTACT_FIELDS:
            row[field] = list(found.get(field) or [])
        row["primary_email"] = record.get("primary_email")
        row["has_direct_contact"] = bool(record.get("has_direct_contact"))
        row["contact_sources"] = record.get("contact_sources") or []
    for key in _LIST_KEYS & row.keys():
        row[key] = row[key] or []
    for key in _NULL_IF_EMPTY & row.keys():
        row[key] = row[key] or None
    row["subscribers_hidden"] = bool(row["subscribers_hidden"])
    return _plain(row)


def upsert(
    result: RunResult,
    url: str,
    key: str,
    table: str = DEFAULT_TABLE,
    *,
    session: requests.Session | None = None,
    timeout: float = 30,
) -> int:
    """Grava os canais do resultado e devolve quantos foram enviados."""
    if not url or not key:
        raise SupabaseError("Defina SUPABASE_URL e SUPABASE_SECRET_KEY no arquivo .env para usar o Supabase.")
    if not url.startswith(("https://", "http://")):
        raise SupabaseError("SUPABASE_URL deve começar com https:// (ex.: https://seu-projeto.supabase.co).")
    if not _TABLE_NAME.match(table):
        raise SupabaseError(f"Nome de tabela inválido: {table!r}.")
    rows = [to_row(record, result.config.extract_contacts) for record in result.records]
    if not rows:
        return 0
    headers = {
        "apikey": key,
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    if key.startswith("eyJ"):  # chave legada (JWT service_role); as novas sb_secret_ vão só no apikey
        headers["Authorization"] = f"Bearer {key}"
    endpoint = f"{url.rstrip('/')}/rest/v1/{table}"
    http = session or requests.Session()
    sent = 0
    for batch in chunks(rows, BATCH_SIZE):
        try:
            response = http.post(
                endpoint,
                params={"on_conflict": "channel_id"},
                headers=headers,
                data=json.dumps(batch, ensure_ascii=False).encode("utf-8"),
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise SupabaseError(f"Falha de rede ao falar com o Supabase: {exc}") from exc
        if response.status_code >= 300:
            raise SupabaseError(_friendly_error(response, table, sent))
        sent += len(batch)
    return sent


def _friendly_error(response: Any, table: str, sent: int) -> str:
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    code = str(body.get("code") or "")
    message = body.get("message") or (response.text or "")[:300]
    partial = f" {sent} canais já tinham sido gravados antes do erro." if sent else ""
    if response.status_code in (401, 403) or code == "42501":
        text = (
            "O Supabase recusou a gravação. Use a secret key (sb_secret_...) ou a service_role em "
            "SUPABASE_SECRET_KEY: a tabela tem RLS ligado e nenhuma política para chaves públicas."
        )
    elif code == "PGRST205" or response.status_code == 404:
        text = f"A tabela {table} não existe no Supabase. Rode sql/supabase_schema.sql no SQL Editor do projeto."
    elif code == "PGRST204":
        text = f"Falta uma coluna na tabela {table} ({message}). Rode sql/supabase_schema.sql para atualizar a estrutura."
    else:
        text = f"O Supabase recusou a gravação (HTTP {response.status_code}): {message}"
    return text + partial


def _plain(value: Any) -> Any:
    """Converte datas e tuplas em tipos que o JSON aceita."""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
