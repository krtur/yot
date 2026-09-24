"""Extração de contatos que o próprio criador publicou em textos públicos.

Lê apenas o texto recebido (descrição do canal e descrições dos vídeos). Não acessa o
e-mail comercial protegido por captcha na aba "Sobre" do YouTube: esse precisa ser
verificado manualmente.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterator
from urllib.parse import parse_qs, unquote, urlparse

CONTACT_FIELDS: tuple[str, ...] = (
    "emails", "whatsapp", "phones", "instagram", "tiktok", "twitter_x", "facebook",
    "linkedin", "kwai", "threads", "telegram", "discord", "twitch", "link_in_bio", "websites",
)
DIRECT_FIELDS: tuple[str, ...] = ("emails", "whatsapp", "phones")

CONTACT_LABELS = {
    "emails": "E-mail", "whatsapp": "WhatsApp", "phones": "Telefone", "instagram": "Instagram",
    "tiktok": "TikTok", "twitter_x": "X/Twitter", "facebook": "Facebook", "linkedin": "LinkedIn",
    "kwai": "Kwai", "threads": "Threads", "telegram": "Telegram", "discord": "Discord",
    "twitch": "Twitch", "link_in_bio": "Link na bio", "websites": "Site",
}

# DDDs válidos no Brasil
VALID_DDD = {
    11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 28, 31, 32, 33, 34, 35, 37, 38,
    41, 42, 43, 44, 45, 46, 47, 48, 49, 51, 53, 54, 55, 61, 62, 63, 64, 65, 66, 67, 68, 69,
    71, 73, 74, 75, 77, 79, 81, 82, 83, 84, 85, 86, 87, 88, 89, 91, 92, 93, 94, 95, 96, 97, 98, 99,
}

# "contato [at] canal [dot] com" -> "contato@canal.com"
_OBF_AT = re.compile(r"\s*[\[\(\{<]\s*(?:at|arroba)\s*[\]\)\}>]\s*", re.IGNORECASE)
_OBF_DOT = re.compile(r"\s*[\[\(\{<]\s*(?:dot|ponto)\s*[\]\)\}>]\s*", re.IGNORECASE)
_EMAIL = re.compile(
    r"(?<![\w.%+-])([A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24})(?![A-Za-z0-9-])"
)
_EMAIL_IGNORED_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
_EMAIL_IGNORED_DOMAINS = {"example.com", "exemplo.com", "email.com", "seuemail.com", "dominio.com"}

_BARE_DOMAINS = (
    r"instagram\.com|tiktok\.com|twitter\.com|x\.com|facebook\.com|fb\.com|fb\.me|linkedin\.com|"
    r"t\.me|telegram\.me|discord\.gg|discord\.com|twitch\.tv|kwai\.com|threads\.net|threads\.com|"
    r"linktr\.ee|beacons\.ai|bio\.link|lnk\.bio|taplink\.cc|linkme\.bio|"
    r"wa\.me|api\.whatsapp\.com|chat\.whatsapp\.com"
)
_URL = re.compile(
    rf"(?i)(?:\bhttps?://|\bwww\.)[^\s<>\"'«»]+|(?<![\w.@/])(?:{_BARE_DOMAINS})/[^\s<>\"'«»]+"
)
_TRAILING = ".,;:!?)]}>…\"'*"

_BR_PHONE = re.compile(
    r"(?<![\w+])"
    r"(?P<cc>\+\s?55[\s.-]?|55[\s.-])?"
    r"(?:\((?P<ddd1>\d{2})\)|(?P<ddd2>\d{2}))"
    r"[\s.-]?"
    r"(?P<p1>9[\s.]?\d{4}|[2-5]\d{3})"
    r"[\s.-]?"
    r"(?P<p2>\d{4})"
    r"(?!\d)"
)
_INTL_PHONE = re.compile(r"(?<![\w+])\+(?!55)(\d[\d\s().-]{8,18}\d)(?!\d)")
_PHONE_CONTEXT = re.compile(r"(?i)(whats|zap|wpp|tel|fone|celular|contato|ligue|liga|sms|phone|comercial)")
_WHATSAPP_WORDS = {"whats", "zap", "wpp"}

_MENTIONS = {
    "instagram": re.compile(r"(?i)\b(?:instagram|insta|ig)\b\s*[:\-–—]?\s*@([A-Za-z0-9._]{2,30})"),
    "tiktok": re.compile(r"(?i)\btik\s?tok\b\s*[:\-–—]?\s*@([A-Za-z0-9._]{2,24})"),
    "twitter_x": re.compile(r"(?i)(?:\btwitter\b\s*[:\-–—]?|\bx\b\s*[:\-–—])\s*@([A-Za-z0-9_]{1,15})"),
    "kwai": re.compile(r"(?i)\bkwai\b\s*[:\-–—]?\s*@([A-Za-z0-9._]{2,30})"),
    "threads": re.compile(r"(?i)\bthreads\b\s*[:\-–—]?\s*@([A-Za-z0-9._]{2,30})"),
}

_IG_RESERVED = {"p", "reel", "reels", "tv", "explore", "accounts", "direct", "about", "legal", "developer"}
_X_RESERVED = {"intent", "share", "hashtag", "search", "i", "home", "explore", "settings", "login",
               "signup", "messages", "notifications"}
_FB_RESERVED = {"sharer", "sharer.php", "share", "share.php", "dialog", "plugins", "watch", "events",
                "photo", "photo.php", "story.php", "permalink.php", "login", "hashtag", "reel", "reels",
                "help", "policies", "ads", "business", "gaming", "marketplace"}
_TWITCH_RESERVED = {"videos", "directory", "downloads", "jobs", "p", "settings", "subscriptions"}
_LINK_IN_BIO = {"linktr.ee", "beacons.ai", "bio.link", "lnk.bio", "taplink.cc", "linkme.bio",
                "campsite.bio", "solo.to", "allmylinks.com", "hoo.be", "linkin.bio"}
# Links que não identificam o criador: encurtadores, lojas/afiliados, checkout, streaming de áudio.
_IGNORED_DOMAINS = (
    "youtube.com", "youtu.be", "youtube-nocookie.com", "google.com", "goo.gl", "forms.gle", "g.co",
    "bit.ly", "tinyurl.com", "cutt.ly", "rebrand.ly", "t.co", "ow.ly", "is.gd", "shorturl.at",
    "encurtador.com.br", "abre.ai", "l.instagram.com", "amzn.to", "a.co", "amazon.com",
    "amazon.com.br", "mercadolivre.com.br", "mercadolivre.com", "mercadolibre.com", "shopee.com.br",
    "shope.ee", "aliexpress.com", "magazineluiza.com.br", "magalu.com", "americanas.com.br",
    "hotmart.com", "kiwify.com.br", "eduzz.com", "monetizze.com.br", "spotify.com", "apple.com",
    "deezer.com", "soundcloud.com", "whatsapp.com",
)


def empty() -> dict[str, list[str]]:
    return {field: [] for field in CONTACT_FIELDS}


def has_direct_contact(found: dict[str, list[str]]) -> bool:
    return any(found.get(field) for field in DIRECT_FIELDS)


def extract(text: str | None) -> dict[str, list[str]]:
    """Contatos encontrados em um texto, por tipo, na ordem em que aparecem."""
    found = empty()
    if not text:
        return found

    def add(kind: str, value: str | None) -> None:
        if value and value not in found[kind]:
            found[kind].append(value)

    readable = _OBF_DOT.sub(".", _OBF_AT.sub("@", text))
    for match in _EMAIL.finditer(readable):
        add("emails", _clean_email(match.group(1)))

    for match in _URL.finditer(text):
        classified = _classify_url(match.group(0).rstrip(_TRAILING))
        if classified:
            add(*classified)

    without_urls = _URL.sub(" ", text)
    for kind, phone in _phones(without_urls):
        add(kind, phone)

    for kind, pattern in _MENTIONS.items():
        for match in pattern.finditer(without_urls):
            add(kind, "@" + match.group(1).rstrip(".").lower())
    return found


def collect(
    channel_text: str | None, video_texts: list[str], min_video_repeats: int = 2
) -> tuple[dict[str, list[str]], list[tuple[str, str, str]]]:
    """Junta os contatos da descrição do canal com os que se repetem nos vídeos.

    Links que aparecem em um único vídeo costumam ser de patrocinador ou referência,
    então só entram os que se repetem em pelo menos `min_video_repeats` vídeos.
    Devolve (contatos por tipo, linhas (tipo, valor, origem)).
    """
    found = extract(channel_text)
    sources = [(kind, value, "descrição do canal") for kind in CONTACT_FIELDS for value in found[kind]]

    texts = [text for text in video_texts if text]
    if texts:
        threshold = max(1, min(min_video_repeats, len(texts)))
        counts: Counter[tuple[str, str]] = Counter()
        for text in texts:
            for kind, values in extract(text).items():
                counts.update((kind, value) for value in values)
        for (kind, value), repeats in counts.most_common():
            if repeats >= threshold and value not in found[kind]:
                found[kind].append(value)
                sources.append((kind, value, f"descrição de {repeats} vídeo(s) recente(s)"))
    return found, sources


# ---------------------------------------------------------------------- apoio
def _clean_email(raw: str) -> str | None:
    email = raw.strip(".").lower()
    domain = email.rsplit("@", 1)[-1]
    if email.endswith(_EMAIL_IGNORED_SUFFIXES) or domain in _EMAIL_IGNORED_DOMAINS:
        return None
    return email


def _e164(digits: str) -> str:
    if len(digits) in (10, 11) and int(digits[:2]) in VALID_DDD:
        return "+55" + digits
    return "+" + digits


def _phones(text: str) -> Iterator[tuple[str, str]]:
    for match in _BR_PHONE.finditer(text):
        ddd = int(match.group("ddd1") or match.group("ddd2"))
        if ddd not in VALID_DDD:
            continue
        raw = match.group(0)
        formatted = bool(match.group("cc") or match.group("ddd1")) or "-" in raw
        keywords = [m.group(1).lower() for m in _PHONE_CONTEXT.finditer(text[max(0, match.start() - 40):match.start()])]
        if not formatted and not keywords:
            continue
        number = re.sub(r"\D", "", match.group("p1")) + match.group("p2")
        kind = "whatsapp" if keywords and keywords[-1] in _WHATSAPP_WORDS else "phones"
        yield kind, f"+55{ddd}{number}"

    for match in _INTL_PHONE.finditer(text):
        raw = match.group(1)
        digits = re.sub(r"\D", "", raw)
        if not 10 <= len(digits) <= 15:
            continue
        if "." in raw and not re.search(r"[\s()-]", raw):  # parece número grande, não telefone
            continue
        yield "phones", "+" + digits


def _host_is(host: str, domains: tuple[str, ...] | set[str]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _classify_url(raw: str) -> tuple[str, str] | None:
    url = raw if re.match(r"(?i)https?://", raw) else "https://" + raw
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None
    for prefix in ("www.", "m.", "mobile.", "web."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    segments = [segment for segment in unquote(parsed.path).split("/") if segment]
    first = segments[0] if segments else ""
    first_lower = first.lower()

    if host in ("wa.me", "api.whatsapp.com"):
        if host == "wa.me":
            digits = re.sub(r"\D", "", first)
        else:
            digits = re.sub(r"\D", "", (parse_qs(parsed.query).get("phone") or [""])[0])
        if 10 <= len(digits) <= 15:
            return "whatsapp", _e164(digits)
        if host == "wa.me" and first_lower == "message" and len(segments) > 1:
            return "whatsapp", f"wa.me/message/{segments[1]}"
        return None
    if host == "instagram.com":
        if first_lower == "stories" and len(segments) > 1:
            return "instagram", "@" + segments[1].lower()
        if first and first_lower not in _IG_RESERVED:
            return "instagram", "@" + first_lower
        return None
    if _host_is(host, ("tiktok.com",)):
        handle = next((segment for segment in segments if segment.startswith("@")), "")
        return ("tiktok", handle.lower()) if len(handle) > 1 else None
    if host in ("twitter.com", "x.com"):
        return ("twitter_x", "@" + first_lower) if first and first_lower not in _X_RESERVED else None
    if host in ("facebook.com", "fb.com", "fb.me"):
        if first_lower == "profile.php":
            profile_id = (parse_qs(parsed.query).get("id") or [""])[0]
            return ("facebook", f"facebook.com/profile.php?id={profile_id}") if profile_id else None
        if first_lower == "groups" and len(segments) > 1:
            return "facebook", f"facebook.com/groups/{segments[1]}"
        return ("facebook", f"facebook.com/{first}") if first and first_lower not in _FB_RESERVED else None
    if host == "linkedin.com":
        if first_lower in ("in", "company", "school") and len(segments) > 1:
            return "linkedin", f"linkedin.com/{first_lower}/{segments[1]}"
        return None
    if host in ("t.me", "telegram.me"):
        return ("telegram", f"t.me/{first}") if first and first_lower not in ("share", "s") else None
    if host == "discord.gg":
        return ("discord", f"discord.gg/{first}") if first else None
    if host == "discord.com":
        if first_lower == "invite" and len(segments) > 1:
            return "discord", f"discord.gg/{segments[1]}"
        return None
    if host == "twitch.tv":
        return ("twitch", f"twitch.tv/{first_lower}") if first and first_lower not in _TWITCH_RESERVED else None
    if _host_is(host, ("kwai.com", "kwai.net")):
        handle = next((segment for segment in segments if segment.startswith("@")), "")
        return ("kwai", handle.lower()) if len(handle) > 1 else None
    if host in ("threads.net", "threads.com"):
        return ("threads", first_lower) if first.startswith("@") and len(first) > 1 else None
    if _host_is(host, _IGNORED_DOMAINS):
        return None
    if host in _LINK_IN_BIO:
        return ("link_in_bio", f"{host}/{first}") if first else None
    if host.endswith(".carrd.co"):
        return "link_in_bio", host
    if "." in host and not host.replace(".", "").isdigit():
        return "websites", host
    return None
