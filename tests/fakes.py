"""YouTube Data API falsa, com um cenário fixo, para testar sem rede e sem gastar cota.

Cenário (NOW = 23/09/2026 15:00 UTC):
- cid(1) Ana Finanças, 120 mil: e-mail, Instagram e WhatsApp na descrição; linktr.ee repetido em 2 vídeos
  e um patrocinador (corretoraxp.com.br) em 1 vídeo só.
- cid(2) Investe Já, 45 mil: e-mail ofuscado, site e TikTok repetido em 2 vídeos.
- cid(3) Canal Parado, 30 mil: último upload há mais de 400 dias.
- cid(4) Mega Finanças, 2 milhões. cid(6) Canal Oculto: inscritos ocultos.
- cid(5) Canal Direto, 5 mil: só aparece como perfil informado (@canaldireto).
- cid(7) Sem Vídeos, 15 mil: playlist de uploads inexistente (404).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from prospector.pipeline import SearchConfig

NOW = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)
NICHE_A = "finanças pessoais"
NICHE_B = "investimentos"


def cid(n: int) -> str:
    return "UC" + f"{n:022d}"


def uploads(n: int) -> str:
    return "UU" + cid(n)[2:]


def _channel(
    n: int,
    title: str,
    subscribers: int,
    *,
    handle: str,
    description: str,
    views: int,
    video_count: int,
    keywords: str = "",
    hidden: bool = False,
) -> dict[str, Any]:
    statistics: dict[str, Any] = {"viewCount": str(views), "videoCount": str(video_count), "hiddenSubscriberCount": hidden}
    if not hidden:
        statistics["subscriberCount"] = str(subscribers)
    return {
        "kind": "youtube#channel",
        "id": cid(n),
        "snippet": {
            "title": title,
            "description": description,
            "customUrl": handle,
            "publishedAt": "2019-03-10T12:00:00.123Z",
            "country": "BR",
            "defaultLanguage": "pt",
        },
        "statistics": statistics,
        "brandingSettings": {"channel": {"keywords": keywords}},
        "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Finance"]},
        "contentDetails": {"relatedPlaylists": {"likes": "", "uploads": uploads(n)}},
    }


def _video(
    video_id: str, n: int, published: str, views: int, likes: int | None, comments: int | None, description: str
) -> dict[str, Any]:
    statistics = {"viewCount": str(views)}
    if likes is not None:
        statistics["likeCount"] = str(likes)
    if comments is not None:
        statistics["commentCount"] = str(comments)
    return {
        "id": video_id,
        "snippet": {"channelId": cid(n), "title": f"Vídeo {video_id}", "description": description, "publishedAt": published},
        "statistics": statistics,
    }


CHANNELS = {
    channel["id"]: channel
    for channel in (
        _channel(
            1, "Ana Finanças", 120_000, handle="@anafinancas", views=9_800_000, video_count=310,
            keywords='finanças "educação financeira" investimentos',
            description=(
                "Educação financeira sem complicação.\n"
                "Contato comercial: contato@anafinancas.com.br\n"
                "Instagram: instagram.com/ana.financas\n"
                "WhatsApp: wa.me/5511987654321"
            ),
        ),
        _channel(
            2, "Investe Já", 45_000, handle="@investeja", views=2_100_000, video_count=140,
            keywords="investimentos renda-fixa",
            description=(
                "Investimentos para quem está começando.\n"
                "Parcerias: parcerias [at] investeja [dot] com\n"
                "Site: www.investeja.com.br"
            ),
        ),
        _channel(3, "Canal Parado", 30_000, handle="@canalparado", views=800_000, video_count=60,
                 description="Canal sobre finanças. Voltamos em breve."),
        _channel(4, "Mega Finanças", 2_000_000, handle="@megafinancas", views=300_000_000, video_count=900,
                 description="O maior canal de finanças."),
        _channel(5, "Canal Direto", 5_000, handle="@canaldireto", views=120_000, video_count=35,
                 description="Fale comigo: canaldireto@gmail.com\nWhatsApp: (21) 99876-5432"),
        _channel(6, "Canal Oculto", 0, handle="@canaloculto", views=50_000, video_count=12,
                 description="", hidden=True),
        _channel(7, "Sem Vídeos", 15_000, handle="@semvideos", views=0, video_count=0, description="Em breve."),
    )
}

VIDEOS = {
    video["id"]: video
    for video in (
        _video("ana00000001", 1, "2026-09-18T12:00:00Z", 50_000, 3_000, 200,
               "Planilha e materiais: linktr.ee/anafinancas\nOferecimento: corretoraxp.com.br"),
        _video("ana00000002", 1, "2026-09-04T12:00:00Z", 30_000, 1_500, 100, "Todos os links: linktr.ee/anafinancas"),
        _video("ana00000003", 1, "2026-08-21T12:00:00Z", 20_000, 1_000, None, "Obrigada por assistir!"),
        _video("inv00000001", 2, "2026-09-10T12:00:00Z", 12_000, 800, 40, "Siga no TikTok: tiktok.com/@investeja"),
        _video("inv00000002", 2, "2026-08-25T12:00:00Z", 9_000, 500, 30, "Me siga no TikTok: tiktok.com/@investeja"),
        _video("par00000001", 3, "2025-08-01T12:00:00Z", 4_000, 100, 10, ""),
        _video("meg00000001", 4, "2026-09-20T12:00:00Z", 900_000, 40_000, 2_000, ""),
        _video("dir00000001", 5, "2026-09-15T12:00:00Z", 1_500, 90, 12, "Novo vídeo toda semana."),
        _video("ocu00000001", 6, "2026-09-01T12:00:00Z", 700, 20, 1, ""),
    )
}

PLAYLISTS = {
    uploads(1): ["ana00000001", "ana00000002", "ana00000003"],
    uploads(2): ["inv00000001", "inv00000002"],
    uploads(3): ["par00000001"],
    uploads(4): ["meg00000001"],
    uploads(5): ["dir00000001"],
    uploads(6): ["ocu00000001"],
}

# (termo, pageToken) -> (canais de cada resultado, próximo token)
SEARCH = {
    (NICHE_A, None): ([1, 1, 3, 4, 1], "P2"),
    (NICHE_A, "P2"): ([1, 6], None),
    (NICHE_B, None): ([2, 2, 1], None),
}

_MESSAGES = {
    "quotaExceeded": "The request cannot be completed because you have exceeded your quota.",
    "backendError": "Backend Error",
    "playlistNotFound": "The playlist identified with the request's playlistId parameter cannot be found.",
}


def error_payload(status: int, reason: str) -> dict[str, Any]:
    if reason == "API_KEY_INVALID":  # formato atual do Google para chave inválida
        message = "API key not valid. Please pass a valid API key."
        return {"error": {
            "code": status, "message": message, "status": "INVALID_ARGUMENT",
            "errors": [{"message": message, "domain": "global", "reason": "badRequest"}],
            "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID",
                         "domain": "googleapis.com"}],
        }}
    message = _MESSAGES.get(reason, reason)
    domain = "youtube.quota" if reason == "quotaExceeded" else "youtube.api"
    return {"error": {"code": status, "message": message, "errors": [{"message": message, "domain": domain, "reason": reason}]}}


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return json.loads(self.text)


class FakeYouTube:
    """Imita requests.Session.get para search, channels, playlistItems e videos."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_once: dict[str, list[tuple[int, str]]] = {}
        self.fail_always: dict[str, tuple[int, str]] = {}

    def get(self, url: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> FakeResponse:
        endpoint = urlparse(url).path.rsplit("/", 1)[-1]
        params = dict(params or {})
        assert params.pop("key", None), "toda chamada precisa levar a chave"
        self.calls.append((endpoint, params))
        queued = self.fail_once.get(endpoint)
        if queued:
            status, reason = queued.pop(0)
            return FakeResponse(status, error_payload(status, reason))
        if endpoint in self.fail_always:
            status, reason = self.fail_always[endpoint]
            return FakeResponse(status, error_payload(status, reason))
        return getattr(self, f"_{endpoint}")(params)

    def count(self, endpoint: str | None = None) -> int:
        return sum(1 for name, _ in self.calls if endpoint in (None, name))

    def close(self) -> None:
        pass

    def _search(self, params: dict[str, Any]) -> FakeResponse:
        ids, token = SEARCH.get((params.get("q"), params.get("pageToken")), ([], None))
        body: dict[str, Any] = {
            "items": [{"id": {"kind": "youtube#video", "videoId": f"v{n}"}, "snippet": {"channelId": cid(n)}} for n in ids]
        }
        if token:
            body["nextPageToken"] = token
        return FakeResponse(200, body)

    def _channels(self, params: dict[str, Any]) -> FakeResponse:
        if "id" in params:
            items = [CHANNELS[channel_id] for channel_id in params["id"].split(",") if channel_id in CHANNELS]
        elif "forHandle" in params:
            wanted = params["forHandle"].lstrip("@").lower()
            items = [c for c in CHANNELS.values() if c["snippet"]["customUrl"].lstrip("@").lower() == wanted]
        else:
            items = []
        body: dict[str, Any] = {"kind": "youtube#channelListResponse", "pageInfo": {"totalResults": len(items)}}
        if items:  # a API real omite "items" quando nada é encontrado
            body["items"] = items
        return FakeResponse(200, body)

    def _playlistItems(self, params: dict[str, Any]) -> FakeResponse:  # noqa: N802 (nome do endpoint)
        ids = PLAYLISTS.get(params.get("playlistId"))
        if ids is None:
            return FakeResponse(404, error_payload(404, "playlistNotFound"))
        limit = int(params.get("maxResults", 5))
        return FakeResponse(200, {"items": [{"contentDetails": {"videoId": video_id}} for video_id in ids[:limit]]})

    def _videos(self, params: dict[str, Any]) -> FakeResponse:
        items = [VIDEOS[video_id] for video_id in params.get("id", "").split(",") if video_id in VIDEOS]
        return FakeResponse(200, {"items": items})


def main_config(**overrides: Any) -> SearchConfig:
    """Configuração principal: 2 nichos x 2 páginas, 10 mil a 1 milhão, ativos em 180 dias, com contatos."""
    params: dict[str, Any] = dict(
        niches=[NICHE_A, NICHE_B],
        profiles=["@canaldireto", "@naoexiste"],
        pages_per_niche=2,
        min_subscribers=10_000,
        max_subscribers=1_000_000,
        active_within_days=180,
        extract_contacts=True,
    )
    params.update(overrides)
    return SearchConfig(**params)
