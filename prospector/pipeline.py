"""Orquestra a coleta: nichos e perfis -> canais -> vídeos recentes -> registros estruturados.

As métricas vêm da API ou de contas simples sobre dados da API (somas, médias e divisões),
como as políticas do YouTube permitem. Não há pontuação, ranking nem classificação de canais.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

from . import contacts as contact_extractor
from .youtube import QuotaExceeded, YouTubeClient, chunks, unique

MODES = ("videos", "canais")
ORDERS = ("relevance", "date", "viewCount", "rating")
ORDER_LABELS = {
    "relevance": "Relevância",
    "date": "Data de publicação",
    "viewCount": "Visualizações",
    "rating": "Avaliação",
}
MODE_LABELS = {"videos": "Vídeos sobre o nicho", "canais": "Canais pelo nome"}
MAX_PAGES_PER_NICHE = 10
MAX_RECENT_VIDEOS = 50
SOURCE_SEARCH = "busca"
SOURCE_PROFILE = "perfil informado"

FILTER_LABELS = {
    "subscribers_out_of_range": "Fora da faixa de inscritos",
    "subscribers_hidden": "Inscritos ocultos (com mínimo definido)",
    "max_channels": "Acima do limite de canais",
    "inactive": "Sem upload no período de atividade",
    "no_contact": "Sem contato publicado",
    "unavailable": "Indisponível na API",
}
STAGE_LABELS = {
    "busca": "Buscando nos nichos",
    "perfis": "Localizando perfis informados",
    "canais": "Carregando dados dos canais",
    "videos": "Listando vídeos recentes",
    "detalhes": "Lendo estatísticas dos vídeos",
}

Progress = Callable[[str, int, int], None]

_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_NAME = re.compile(r"^[\w.\-]{3,100}$")
_YOUTUBE_URL = re.compile(r"(?i)^(?:https?://)?(?:[\w-]+\.)*(?:youtube\.com|youtu\.be|youtube-nocookie\.com)(?:[/?#]|$)")
_YOUTUBE_RESERVED = {
    "watch", "results", "feed", "playlist", "channel", "user", "c", "shorts", "live", "embed", "v",
    "hashtag", "gaming", "premium", "account", "about", "t", "redirect", "post", "source", "music",
}
_KEYWORDS = re.compile(r'"([^"]+)"|(\S+)')


@dataclass
class SearchConfig:
    """Parâmetros de uma execução. Valores zerados nos filtros significam "sem filtro"."""

    niches: list[str] = field(default_factory=list)
    profiles: list[str] = field(default_factory=list)
    mode: str = "videos"
    pages_per_niche: int = 1
    region: str | None = "BR"
    language: str | None = "pt"
    order: str = "relevance"
    published_after: date | None = None
    min_subscribers: int | None = None
    max_subscribers: int | None = None
    active_within_days: int | None = None
    recent_videos: int = 10
    extract_contacts: bool = False
    only_with_contact: bool = False
    max_channels: int | None = None
    min_video_repeats: int = 2

    def __post_init__(self) -> None:
        self.niches = unique(" ".join(str(niche).split()) for niche in self.niches)
        self.profiles = unique(str(profile).strip() for profile in self.profiles)
        self.pages_per_niche = min(max(int(self.pages_per_niche or 1), 1), MAX_PAGES_PER_NICHE)
        self.recent_videos = min(max(int(self.recent_videos or 0), 0), MAX_RECENT_VIDEOS)
        self.min_video_repeats = max(int(self.min_video_repeats or 1), 1)
        self.region = (self.region or "").strip().upper() or None
        self.language = (self.language or "").strip().lower() or None
        if isinstance(self.published_after, datetime):
            self.published_after = self.published_after.date()
        elif isinstance(self.published_after, str):
            self.published_after = date.fromisoformat(self.published_after) if self.published_after else None
        for name in ("min_subscribers", "max_subscribers", "active_within_days", "max_channels"):
            value = getattr(self, name)
            setattr(self, name, int(value) if value and int(value) > 0 else None)

    @property
    def videos_per_channel(self) -> int:
        """Vídeos lidos por canal. O filtro de atividade precisa de pelo menos o mais recente."""
        if self.active_within_days and self.recent_videos == 0:
            return 1
        return self.recent_videos

    def validate(self) -> None:
        if not self.niches and not self.profiles:
            raise ValueError("Informe ao menos um nicho ou um perfil.")
        if self.mode not in MODES:
            raise ValueError("Modo de busca inválido: use 'videos' ou 'canais'.")
        if self.order not in ORDERS:
            raise ValueError(f"Ordenação inválida: use {', '.join(ORDERS)}.")
        if self.only_with_contact and not self.extract_contacts:
            raise ValueError("Para mostrar só canais com contato, ative a extração de contatos.")
        if self.min_subscribers and self.max_subscribers and self.min_subscribers > self.max_subscribers:
            raise ValueError("O mínimo de inscritos está maior que o máximo.")

    def estimate(self) -> dict[str, int]:
        """Teto de consumo de cota desta configuração, calculado sem chamar a API."""
        searches = len(self.niches) * self.pages_per_niche
        from_search = searches * 50
        resolve_units = 0
        for raw in self.profiles:
            parsed = parse_profile(raw)
            if parsed:
                resolve_units += {"id": 0, "handle": 1, "video": 1}.get(parsed[0], 2)
        candidates = from_search + len(self.profiles)
        analyzed = (min(from_search, self.max_channels) if self.max_channels else from_search) + len(self.profiles)
        units = resolve_units + math.ceil(candidates / 50)
        per_channel = self.videos_per_channel
        if per_channel and analyzed:
            units += analyzed + math.ceil(analyzed * per_channel / 50)
        return {"search_calls": searches, "max_channels": candidates, "max_units": units}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["published_after"] = self.published_after.isoformat() if self.published_after else None
        return data


@dataclass
class RunResult:
    config: SearchConfig
    records: list[dict[str, Any]]
    contact_rows: list[dict[str, str]]
    stats: dict[str, Any]
    started_at: datetime
    finished_at: datetime


# ----------------------------------------------------------------------- perfis
def parse_profile(raw: str | None) -> tuple[str, str] | None:
    """Interpreta @handle, ID, URL de canal ou de vídeo. Devolve (tipo, valor) ou None.

    Tipos: "id", "handle", "username", "name" (handle ou URL personalizada) e "video".
    """
    text = (raw or "").strip().strip("<>\"'")
    if not text:
        return None
    if _CHANNEL_ID.match(text):
        return "id", text
    if text.startswith("@"):
        handle = re.split(r"[/?#]", text[1:], maxsplit=1)[0]
        return ("handle", handle) if handle and not re.search(r"\s", handle) else None
    if _YOUTUBE_URL.match(text):
        return _parse_youtube_url(text)
    if _NAME.match(text):
        return "name", text
    return None


def _parse_youtube_url(text: str) -> tuple[str, str] | None:
    url = text if re.match(r"(?i)^https?://", text) else "https://" + text
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
    if host.endswith("youtu.be"):
        return ("video", segments[0]) if segments and _VIDEO_ID.match(segments[0]) else None
    if not segments:
        return None
    first, rest = segments[0], segments[1:]
    if first.startswith("@"):
        return ("handle", first[1:]) if len(first) > 1 else None
    first_lower = first.lower()
    if first_lower == "channel" and rest and _CHANNEL_ID.match(rest[0]):
        return "id", rest[0]
    if first_lower == "user" and rest:
        return "username", rest[0]
    if first_lower == "c" and rest:
        return "name", rest[0]
    if first_lower == "watch":
        video = (parse_qs(parsed.query).get("v") or [""])[0]
        return ("video", video) if _VIDEO_ID.match(video) else None
    if first_lower in ("shorts", "live", "embed", "v") and rest and _VIDEO_ID.match(rest[0]):
        return "video", rest[0]
    if first_lower not in _YOUTUBE_RESERVED and _NAME.match(first):
        return "name", first
    return None


def _lookup(client: YouTubeClient, kind: str, value: str) -> dict[str, Any] | None:
    if kind == "handle":
        return client.channel_by_handle(value)
    if kind == "username":
        return client.channel_by_username(value) or client.channel_by_handle(value)
    return client.channel_by_handle(value) or client.channel_by_username(value)


# ------------------------------------------------------------------------ fluxo
def run(
    client: YouTubeClient,
    cfg: SearchConfig,
    progress: Progress | None = None,
    *,
    now: datetime | None = None,
) -> RunResult:
    """Executa a coleta. Cota esgotada no meio do caminho vira aviso e resultado parcial."""
    cfg.validate()
    notify: Progress = progress or (lambda stage, done, total: None)
    started = now or datetime.now(timezone.utc)
    usage_start = dict(client.run_usage)
    cache_start = client.cache_hits
    warnings: list[str] = []
    filtered: Counter[str] = Counter()

    hits, found_by, search_stopped = _search(client, cfg, notify, warnings)
    channels, profile_inputs, unresolved, profiles_stopped = _resolve_profiles(client, cfg.profiles, notify, warnings)
    load_stopped = _load_channels(client, [*hits, *profile_inputs], channels, notify, warnings)

    profile_ids = [channel_id for channel_id in profile_inputs if channel_id in channels]
    for channel_id, raws in profile_inputs.items():
        if channel_id not in channels:
            unresolved.extend(raws)
    profile_set = set(profile_ids)

    candidates: list[str] = []
    for channel_id in hits:
        if channel_id in profile_set:
            continue
        channel = channels.get(channel_id)
        if channel is None:
            if not load_stopped:
                filtered["unavailable"] += 1
            continue
        subscribers, hidden = _subscribers(channel)
        if cfg.min_subscribers and (hidden or subscribers is None):
            filtered["subscribers_hidden"] += 1
            continue
        if subscribers is not None and (
            (cfg.min_subscribers and subscribers < cfg.min_subscribers)
            or (cfg.max_subscribers and subscribers > cfg.max_subscribers)
        ):
            filtered["subscribers_out_of_range"] += 1
            continue
        candidates.append(channel_id)

    if cfg.max_channels and len(candidates) > cfg.max_channels:
        candidates.sort(key=lambda cid: (-hits[cid], -(_subscribers(channels[cid])[0] or 0)))
        filtered["max_channels"] += len(candidates) - cfg.max_channels
        candidates = candidates[: cfg.max_channels]
    selected = [*candidates, *profile_ids]

    videos, videos_complete = _recent_videos(
        client, [channels[channel_id] for channel_id in selected], cfg.videos_per_channel, notify, warnings
    )

    records: list[dict[str, Any]] = []
    for channel_id in selected:
        origins = [SOURCE_SEARCH] if channel_id in hits else []
        niches = list(found_by.get(channel_id, []))
        if channel_id in profile_set:
            origins.append(SOURCE_PROFILE)
            niches.append(SOURCE_PROFILE)
        records.append(
            build_record(
                channels[channel_id],
                videos.get(channel_id, []),
                found_by=niches,
                search_hits=hits.get(channel_id, 0),
                source=" + ".join(origins),
                extract_contacts=cfg.extract_contacts,
                min_video_repeats=cfg.min_video_repeats,
                now=started,
            )
        )

    if cfg.active_within_days:
        if videos_complete:
            kept = []
            for record in records:
                days = record["days_since_last_upload"]
                if record["channel_id"] not in profile_set and (days is None or days > cfg.active_within_days):
                    filtered["inactive"] += 1
                    continue
                kept.append(record)
            records = kept
        else:
            warnings.append("O filtro de atividade não foi aplicado porque a leitura dos vídeos foi interrompida.")

    if cfg.only_with_contact:
        kept = []
        for record in records:
            has_any = any(record["contacts"][kind] for kind in contact_extractor.CONTACT_FIELDS)
            if record["channel_id"] not in profile_set and not has_any:
                filtered["no_contact"] += 1
                continue
            kept.append(record)
        records = kept

    records.sort(key=lambda r: (r["subscribers"] is None, -(r["subscribers"] or 0), r["title"].lower()))

    contact_rows = [
        {
            "channel_id": record["channel_id"],
            "title": record["title"],
            "handle": record["handle"],
            "url": record["url"],
            "type": source["type"],
            "type_label": contact_extractor.CONTACT_LABELS[source["type"]],
            "value": source["value"],
            "origin": source["origin"],
        }
        for record in records
        for source in record["contact_sources"]
    ]

    finished = now or datetime.now(timezone.utc)
    stats = {
        "channels_discovered": len(hits),
        "channels_loaded": len(channels),
        "channels_in_report": len(records),
        "with_email": sum(1 for record in records if record["primary_email"]),
        "with_direct_contact": sum(1 for record in records if record["has_direct_contact"]),
        "filtered": dict(filtered),
        "unresolved_profiles": unique(unresolved),
        "warnings": warnings,
        "interrupted": search_stopped or profiles_stopped or load_stopped or not videos_complete,
        "usage_this_run": {bucket: client.run_usage[bucket] - usage_start.get(bucket, 0) for bucket in client.run_usage},
        "cache_hits": client.cache_hits - cache_start,
        "quota_today": client.quota_today(),
    }
    return RunResult(cfg, records, contact_rows, stats, started, finished)


def _search(
    client: YouTubeClient, cfg: SearchConfig, notify: Progress, warnings: list[str]
) -> tuple[Counter[str], dict[str, list[str]], bool]:
    hits: Counter[str] = Counter()
    found_by: dict[str, list[str]] = defaultdict(list)
    total = len(cfg.niches) * cfg.pages_per_niche
    kind = "video" if cfg.mode == "videos" else "channel"
    published_after = (
        datetime.combine(cfg.published_after, datetime.min.time(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if cfg.published_after
        else None
    )
    done = 0
    for niche in cfg.niches:
        token: str | None = None
        for _ in range(cfg.pages_per_niche):
            try:
                ids, token = client.search_page(
                    niche,
                    kind=kind,
                    page_token=token,
                    region=cfg.region,
                    language=cfg.language,
                    order=cfg.order,
                    published_after=published_after,
                )
            except QuotaExceeded as exc:
                warnings.append(f'Busca interrompida no nicho "{niche}". {exc}')
                notify("busca", total, total)
                return hits, found_by, True
            done += 1
            notify("busca", done, total)
            for channel_id in ids:
                hits[channel_id] += 1
                if niche not in found_by[channel_id]:
                    found_by[channel_id].append(niche)
            if not token:
                break
    if total:
        notify("busca", total, total)
    return hits, found_by, False


def _resolve_profiles(
    client: YouTubeClient, profiles: list[str], notify: Progress, warnings: list[str]
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]], list[str], bool]:
    loaded: dict[str, dict[str, Any]] = {}
    inputs: dict[str, list[str]] = defaultdict(list)
    unresolved: list[str] = []
    videos: dict[str, list[str]] = defaultdict(list)
    stopped = False
    for index, raw in enumerate(profiles, 1):
        parsed = parse_profile(raw)
        if parsed is None:
            unresolved.append(raw)
        elif parsed[0] == "id":
            inputs[parsed[1]].append(raw)
        elif parsed[0] == "video":
            videos[parsed[1]].append(raw)
        elif stopped:
            unresolved.append(raw)
        else:
            try:
                channel = _lookup(client, *parsed)
            except QuotaExceeded as exc:
                warnings.append(f"Localização de perfis interrompida. {exc}")
                stopped = True
                unresolved.append(raw)
            else:
                if channel:
                    loaded[channel["id"]] = channel
                    inputs[channel["id"]].append(raw)
                else:
                    unresolved.append(raw)
        notify("perfis", index, len(profiles))

    if videos and not stopped:
        try:
            for video in client.videos_by_id(list(videos)):
                channel_id = (video.get("snippet") or {}).get("channelId")
                if channel_id:
                    inputs[channel_id].extend(videos.pop(video.get("id"), []))
        except QuotaExceeded as exc:
            warnings.append(f"Localização de perfis interrompida. {exc}")
            stopped = True
    for raws in videos.values():
        unresolved.extend(raws)
    return loaded, inputs, unresolved, stopped


def _load_channels(
    client: YouTubeClient,
    wanted: list[str],
    channels: dict[str, dict[str, Any]],
    notify: Progress,
    warnings: list[str],
) -> bool:
    missing = [channel_id for channel_id in unique(wanted) if channel_id not in channels]
    batches = list(chunks(missing, 50))
    for index, batch in enumerate(batches, 1):
        try:
            for channel in client.channels_by_id(batch):
                channels[channel["id"]] = channel
        except QuotaExceeded as exc:
            warnings.append(f"Carregamento dos canais interrompido. {exc}")
            return True
        notify("canais", index, len(batches))
    return False


def _recent_videos(
    client: YouTubeClient,
    channels: list[dict[str, Any]],
    per_channel: int,
    notify: Progress,
    warnings: list[str],
) -> tuple[dict[str, list[dict[str, Any]]], bool]:
    found: dict[str, list[dict[str, Any]]] = {channel["id"]: [] for channel in channels}
    if per_channel <= 0 or not channels:
        return found, True
    owner: dict[str, str] = {}
    try:
        for index, channel in enumerate(channels, 1):
            for video_id in client.recent_video_ids(_uploads_playlist(channel), per_channel):
                owner.setdefault(video_id, channel["id"])
            notify("videos", index, len(channels))
        batches = list(chunks(list(owner), 50))
        for index, batch in enumerate(batches, 1):
            for video in client.videos_by_id(batch):
                channel_id = owner.get(video.get("id", ""))
                if channel_id:
                    found[channel_id].append(video)
            notify("detalhes", index, len(batches))
    except QuotaExceeded as exc:
        warnings.append(f"Leitura dos vídeos recentes interrompida. {exc}")
        return found, False
    return found, True


# ---------------------------------------------------------------------- registro
def build_record(
    channel: dict[str, Any],
    videos: list[dict[str, Any]],
    *,
    found_by: list[str],
    search_hits: int,
    source: str,
    extract_contacts: bool,
    min_video_repeats: int,
    now: datetime,
) -> dict[str, Any]:
    """Monta o registro estruturado de um canal a partir das respostas da API."""
    snippet = channel.get("snippet") or {}
    statistics = channel.get("statistics") or {}
    branding = (channel.get("brandingSettings") or {}).get("channel") or {}
    channel_id = channel["id"]
    handle = snippet.get("customUrl") or ""
    url = (
        f"https://www.youtube.com/{handle}"
        if handle.startswith("@")
        else f"https://www.youtube.com/channel/{channel_id}"
    )
    subscribers, hidden = _subscribers(channel)
    description = snippet.get("description") or branding.get("description") or ""

    ordered = sorted(
        videos,
        key=lambda video: parse_datetime((video.get("snippet") or {}).get("publishedAt"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    recent = [_video_summary(video) for video in ordered]
    views = [video["views"] for video in recent if video["views"] is not None]
    likes = [video["likes"] for video in recent if video["likes"] is not None]
    comments = [video["comments"] for video in recent if video["comments"] is not None]

    engagement = None
    with_likes = [video for video in recent if video["likes"] is not None and video["views"]]
    total_views = sum(video["views"] for video in with_likes)
    if total_views:
        interactions = sum(video["likes"] + (video["comments"] or 0) for video in with_likes)
        engagement = round(interactions / total_views, 6)

    dates = [moment for moment in (parse_datetime(video["published_at"]) for video in recent) if moment]
    last_upload = max(dates) if dates else None
    interval = (
        round((max(dates) - min(dates)).total_seconds() / 86400 / (len(dates) - 1), 1) if len(dates) >= 2 else None
    )

    if extract_contacts:
        video_texts = [(video.get("snippet") or {}).get("description") or "" for video in ordered]
        found, raw_sources = contact_extractor.collect(description, video_texts, min_video_repeats)
    else:
        found, raw_sources = contact_extractor.empty(), []

    return {
        "channel_id": channel_id,
        "title": snippet.get("title") or "",
        "handle": handle,
        "url": url,
        "about_url": url + "/about",
        "subscribers": subscribers,
        "subscribers_hidden": hidden,
        "total_views": _int(statistics.get("viewCount")),
        "video_count": _int(statistics.get("videoCount")),
        "country": snippet.get("country") or branding.get("country") or "",
        "default_language": snippet.get("defaultLanguage") or branding.get("defaultLanguage") or "",
        "channel_created_at": _iso(snippet.get("publishedAt")),
        "topics": _topics(channel),
        "keywords": _keywords(branding.get("keywords")),
        "found_by": found_by,
        "search_hits": search_hits,
        "source": source,
        "description": description,
        "recent_videos_analyzed": len(recent),
        "avg_views_recent": _mean(views),
        "avg_likes_recent": _mean(likes),
        "avg_comments_recent": _mean(comments),
        "engagement_rate_recent": engagement,
        "last_upload_at": last_upload.isoformat() if last_upload else None,
        "days_since_last_upload": max((now - last_upload).days, 0) if last_upload else None,
        "avg_days_between_uploads": interval,
        "recent_videos": recent,
        "collected_at": now.isoformat(),
        "contacts": found,
        "primary_email": found["emails"][0] if found["emails"] else "",
        "has_direct_contact": contact_extractor.has_direct_contact(found),
        "contact_sources": [{"type": kind, "value": value, "origin": origin} for kind, value, origin in raw_sources],
    }


def niche_summary(records: list[dict[str, Any]], top_keywords: int = 30) -> dict[str, list[dict[str, Any]]]:
    """Contagens simples por nicho, palavra-chave declarada e tópico do YouTube."""
    niches: dict[str, dict[str, Any]] = {}
    for record in records:
        for niche in record["found_by"]:
            row = niches.setdefault(
                niche,
                {"niche": niche, "channels": 0, "subscribers_sum": 0, "with_email": 0, "with_direct_contact": 0},
            )
            row["channels"] += 1
            row["subscribers_sum"] += record["subscribers"] or 0
            row["with_email"] += int(bool(record["primary_email"]))
            row["with_direct_contact"] += int(bool(record["has_direct_contact"]))

    keywords: Counter[str] = Counter()
    for record in records:
        keywords.update({keyword.lower() for keyword in record["keywords"]})
    topics: Counter[str] = Counter(topic for record in records for topic in set(record["topics"]))

    return {
        "niches": sorted(niches.values(), key=lambda row: (-row["channels"], row["niche"])),
        "keywords": [
            {"keyword": keyword, "channels": count}
            for keyword, count in sorted(keywords.items(), key=lambda item: (-item[1], item[0]))[:top_keywords]
        ],
        "topics": [
            {"topic": topic, "channels": count}
            for topic, count in sorted(topics.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


# ------------------------------------------------------------------------- apoio
def parse_datetime(value: Any) -> datetime | None:
    """Converte datas ISO 8601 da API (com 'Z' e frações variadas) em datetime com fuso."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    text = str(value).strip()
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    text = re.sub(r"\.(\d+)", lambda match: "." + (match.group(1) + "000000")[:6], text, count=1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> str | None:
    parsed = parse_datetime(value)
    return parsed.isoformat() if parsed else None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _subscribers(channel: dict[str, Any]) -> tuple[int | None, bool]:
    statistics = channel.get("statistics") or {}
    if statistics.get("hiddenSubscriberCount"):
        return None, True
    return _int(statistics.get("subscriberCount")), False


def _uploads_playlist(channel: dict[str, Any]) -> str | None:
    uploads = ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    if uploads:
        return uploads
    channel_id = channel.get("id", "")
    return "UU" + channel_id[2:] if channel_id.startswith("UC") else None


def _video_summary(video: dict[str, Any]) -> dict[str, Any]:
    snippet = video.get("snippet") or {}
    statistics = video.get("statistics") or {}
    video_id = video.get("id", "")
    return {
        "video_id": video_id,
        "title": snippet.get("title") or "",
        "published_at": _iso(snippet.get("publishedAt")),
        "views": _int(statistics.get("viewCount")),
        "likes": _int(statistics.get("likeCount")),
        "comments": _int(statistics.get("commentCount")),
        "url": f"https://www.youtube.com/watch?v={video_id}",
    }


def _topics(channel: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for url in (channel.get("topicDetails") or {}).get("topicCategories") or []:
        name = unquote(str(url).rstrip("/").rsplit("/", 1)[-1]).replace("_", " ").strip()
        if name and name not in names:
            names.append(name)
    return names


def _keywords(raw: str | None) -> list[str]:
    words: list[str] = []
    seen: set[str] = set()
    for quoted, bare in _KEYWORDS.findall(raw or ""):
        word = " ".join((quoted or bare).split()).strip(",;")
        if word and word.lower() not in seen:
            seen.add(word.lower())
            words.append(word)
    return words
