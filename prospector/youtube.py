"""Cliente enxuto da YouTube Data API v3, com cache local e controle de cota.

Cota padrão do Google (documentação oficial, set/2026): 100 chamadas de search.list
por dia e 10.000 unidades por dia para os demais endpoints. channels.list,
playlistItems.list e videos.list custam 1 unidade cada. A cota renova à
meia-noite no horário do Pacífico.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator
from zoneinfo import ZoneInfo

import requests

API_BASE = "https://www.googleapis.com/youtube/v3"
CHANNEL_PARTS = "snippet,statistics,brandingSettings,topicDetails,contentDetails"
DEFAULT_SEARCH_CALLS_PER_DAY = 100
DEFAULT_UNITS_PER_DAY = 10_000

_PACIFIC = ZoneInfo("America/Los_Angeles")
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_RETRYABLE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "backendError", "internalError"}
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}


class YouTubeAPIError(RuntimeError):
    """Erro da YouTube Data API, com mensagem pronta para mostrar ao usuário."""


class QuotaExceeded(YouTubeAPIError):
    """Cota diária esgotada, no Google ou no limite local configurado."""

    def __init__(self, bucket: str, message: str) -> None:
        super().__init__(message)
        self.bucket = bucket


def pacific_day() -> str:
    """Dia corrente no fuso do Pacífico, que é quando a cota do Google renova."""
    return datetime.now(_PACIFIC).date().isoformat()


def chunks(items: list[str], size: int = 50) -> Iterator[list[str]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def unique(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


class LocalStore:
    """SQLite local com cache de respostas (TTL) e contador diário de cota."""

    def __init__(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY, created REAL NOT NULL, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS quota (
                    day TEXT NOT NULL, bucket TEXT NOT NULL, used INTEGER NOT NULL,
                    PRIMARY KEY (day, bucket));
                """
            )
            self._db.commit()

    def get(self, key: str, ttl_seconds: float) -> Any | None:
        if ttl_seconds <= 0:
            return None
        with self._lock:
            row = self._db.execute("SELECT created, body FROM cache WHERE key = ?", (key,)).fetchone()
        if row is None or time.time() - row[0] > ttl_seconds:
            return None
        return json.loads(row[1])

    def put(self, key: str, body: Any) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO cache (key, created, body) VALUES (?, ?, ?)",
                (key, time.time(), json.dumps(body, ensure_ascii=False)),
            )
            self._db.commit()

    def purge(self, older_than_seconds: float) -> None:
        """Remove respostas antigas do cache e contadores de dias anteriores."""
        with self._lock:
            self._db.execute("DELETE FROM cache WHERE created < ?", (time.time() - older_than_seconds,))
            self._db.execute("DELETE FROM quota WHERE day < ?", (pacific_day(),))
            self._db.commit()

    def used(self, bucket: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT used FROM quota WHERE day = ? AND bucket = ?", (pacific_day(), bucket)
            ).fetchone()
        return int(row[0]) if row else 0

    def add(self, bucket: str, amount: int = 1) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO quota (day, bucket, used) VALUES (?, ?, ?) "
                "ON CONFLICT(day, bucket) DO UPDATE SET used = used + excluded.used",
                (pacific_day(), bucket, amount),
            )
            self._db.commit()


class YouTubeClient:
    """Acesso somente leitura a dados públicos do YouTube, com chave de API (sem OAuth)."""

    def __init__(
        self,
        api_key: str,
        *,
        cache_path: str | Path = ".cache/youtube.sqlite",
        cache_ttl_hours: float = 24,
        search_calls_per_day: int = DEFAULT_SEARCH_CALLS_PER_DAY,
        units_per_day: int = DEFAULT_UNITS_PER_DAY,
        session: requests.Session | None = None,
        timeout: float = 30,
        max_retries: int = 3,
    ) -> None:
        if not api_key:
            raise YouTubeAPIError("Informe a chave da API: defina YOUTUBE_API_KEY no arquivo .env.")
        self._key = api_key
        self.store = LocalStore(cache_path)
        self.ttl_seconds = max(cache_ttl_hours, 0) * 3600
        self.limits = {"search": search_calls_per_day, "units": units_per_day}
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.run_usage = {"search": 0, "units": 0}
        self.cache_hits = 0
        self._sleep = time.sleep
        self.store.purge(max(self.ttl_seconds, 3600))

    # ------------------------------------------------------------------ cota
    def quota_today(self) -> dict[str, dict[str, int]]:
        return {
            bucket: {"used": self.store.used(bucket), "limit": limit}
            for bucket, limit in self.limits.items()
        }

    # --------------------------------------------------------------- chamadas
    def search_page(
        self,
        query: str,
        *,
        kind: str = "video",
        page_token: str | None = None,
        region: str | None = None,
        language: str | None = None,
        order: str = "relevance",
        published_after: str | None = None,
    ) -> tuple[list[str], str | None]:
        """Uma página de busca (até 50 resultados). Devolve IDs de canal (com repetição) e o token da próxima."""
        body = self._request(
            "search",
            {
                "part": "snippet",
                "q": query,
                "type": kind,
                "maxResults": 50,
                "pageToken": page_token,
                "regionCode": region,
                "relevanceLanguage": language,
                "order": order,
                "publishedAfter": published_after,
                "fields": "nextPageToken,items(id/channelId,snippet/channelId)",
            },
        )
        ids: list[str] = []
        for item in body.get("items", []):
            channel_id = (item.get("snippet") or {}).get("channelId") or (item.get("id") or {}).get("channelId")
            if channel_id:
                ids.append(channel_id)
        return ids, body.get("nextPageToken")

    def channels_by_id(self, ids: Iterable[str]) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for chunk in chunks(unique(ids)):
            found.extend(self._request("channels", {"part": CHANNEL_PARTS, "id": ",".join(chunk)}).get("items", []))
        return found

    def channel_by_handle(self, handle: str) -> dict[str, Any] | None:
        items = self._request("channels", {"part": CHANNEL_PARTS, "forHandle": handle}).get("items", [])
        return items[0] if items else None

    def channel_by_username(self, username: str) -> dict[str, Any] | None:
        items = self._request("channels", {"part": CHANNEL_PARTS, "forUsername": username}).get("items", [])
        return items[0] if items else None

    def recent_video_ids(self, uploads_playlist_id: str | None, limit: int) -> list[str]:
        if not uploads_playlist_id or limit <= 0:
            return []
        body = self._request(
            "playlistItems",
            {
                "part": "contentDetails",
                "playlistId": uploads_playlist_id,
                "maxResults": min(limit, 50),
                "fields": "items/contentDetails/videoId",
            },
        )
        return [
            item["contentDetails"]["videoId"]
            for item in body.get("items", [])
            if (item.get("contentDetails") or {}).get("videoId")
        ]

    def videos_by_id(self, ids: Iterable[str]) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for chunk in chunks(unique(ids)):
            body = self._request(
                "videos",
                {
                    "part": "snippet,statistics",
                    "id": ",".join(chunk),
                    "fields": "items(id,snippet(channelId,title,description,publishedAt),"
                    "statistics(viewCount,likeCount,commentCount))",
                },
            )
            found.extend(body.get("items", []))
        return found

    # ---------------------------------------------------------------- núcleo
    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        query = {key: value for key, value in params.items() if value not in (None, "")}
        cache_key = hashlib.sha256(
            f"{endpoint}|{json.dumps(query, sort_keys=True, default=str)}".encode()
        ).hexdigest()
        cached = self.store.get(cache_key, self.ttl_seconds)
        if cached is not None:
            self.cache_hits += 1
            return cached

        bucket = "search" if endpoint == "search" else "units"
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            if self.store.used(bucket) >= self.limits[bucket]:
                raise QuotaExceeded(bucket, _local_limit_message(bucket, self.limits[bucket]))
            self.store.add(bucket)
            self.run_usage[bucket] += 1
            last_try = attempt == self.max_retries

            try:
                response = self.session.get(
                    f"{API_BASE}/{endpoint}", params={**query, "key": self._key}, timeout=self.timeout
                )
            except requests.RequestException as exc:
                if last_try:
                    raise YouTubeAPIError(f"Falha de rede ao consultar {endpoint}: {exc}") from exc
                self._sleep(delay)
                delay *= 2
                continue

            if response.status_code == 200:
                body = response.json()
                self.store.put(cache_key, body)
                return body

            reasons, message = _error_details(response)
            if reasons & _QUOTA_REASONS:
                raise QuotaExceeded(
                    bucket,
                    "A cota diária da YouTube Data API acabou no Google. Ela renova à meia-noite "
                    "no horário do Pacífico (4h ou 5h em Brasília, conforme o horário de verão dos EUA).",
                )
            if response.status_code == 404:  # ex.: canal sem vídeos (playlistNotFound)
                empty: dict[str, Any] = {"items": []}
                self.store.put(cache_key, empty)
                return empty
            retryable = response.status_code in _RETRYABLE_STATUS or bool(reasons & _RETRYABLE_REASONS)
            if retryable and not last_try:
                self._sleep(delay)
                delay *= 2
                continue
            raise YouTubeAPIError(_friendly_error(response.status_code, reasons, message))

        raise YouTubeAPIError(f"Não foi possível consultar {endpoint}.")


def _error_details(response: requests.Response) -> tuple[set[str], str]:
    try:
        error = response.json().get("error", {})
    except ValueError:
        return set(), response.text[:300]
    reasons = {e.get("reason", "") for e in error.get("errors", []) if isinstance(e, dict)}
    reasons |= {d.get("reason", "") for d in error.get("details", []) if isinstance(d, dict)}
    reasons.discard("")
    return reasons, error.get("message", "")


def _friendly_error(status: int, reasons: set[str], message: str) -> str:
    if reasons & {"keyInvalid", "API_KEY_INVALID"} or "API key not valid" in message:
        return "Chave de API inválida. Confira o valor de YOUTUBE_API_KEY."
    if reasons & {"accessNotConfigured", "SERVICE_DISABLED"}:
        return (
            "A YouTube Data API v3 não está ativada no projeto do Google Cloud desta chave. "
            "Ative em APIs e serviços > Biblioteca."
        )
    if reasons & {
        "ipRefererBlocked",
        "API_KEY_IP_ADDRESS_BLOCKED",
        "API_KEY_HTTP_REFERRER_BLOCKED",
        "API_KEY_SERVICE_BLOCKED",
    }:
        return (
            "As restrições da chave (IP, referer ou APIs permitidas) bloquearam a chamada. "
            "Revise a chave no Google Cloud."
        )
    detail = ", ".join(sorted(reasons)) or "sem motivo informado"
    return f"A YouTube Data API recusou a chamada (HTTP {status}, {detail}): {message}"


def _local_limit_message(bucket: str, limit: int) -> str:
    what = "buscas" if bucket == "search" else "unidades de cota"
    flag = "--max-buscas-dia" if bucket == "search" else "--max-unidades-dia"
    return (
        f"Limite diário de {limit} {what} atingido. A cota renova à meia-noite no horário do "
        f"Pacífico; se o seu projeto tiver cota maior, ajuste {flag}."
    )
