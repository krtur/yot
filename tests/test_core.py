from __future__ import annotations

import pytest

from fakes import NICHE_A, NICHE_B, NOW, cid, main_config
from prospector import contacts
from prospector.pipeline import SearchConfig, parse_profile, run
from prospector.youtube import YouTubeAPIError

MAIN_TITLES = ["Ana Finanças", "Investe Já", "Canal Direto"]


def titles(result) -> list[str]:
    return [record["title"] for record in result.records]


# ------------------------------------------------------------------- contatos
def test_extract_reads_contacts_published_in_the_text():
    found = contacts.extract(
        "Contato: contato@anafinancas.com.br\nInstagram: instagram.com/ana.financas\nWhatsApp: wa.me/5511987654321"
    )
    assert found["emails"] == ["contato@anafinancas.com.br"]
    assert found["instagram"] == ["@ana.financas"]
    assert found["whatsapp"] == ["+5511987654321"]


def test_extract_handles_obfuscated_email_and_brazilian_phone():
    found = contacts.extract("Parcerias: parcerias [at] investeja [dot] com\nWhatsApp: (21) 99876-5432")
    assert found["emails"] == ["parcerias@investeja.com"]
    assert found["whatsapp"] == ["+5521998765432"]
    assert contacts.has_direct_contact(found)


def test_collect_ignores_links_present_in_a_single_video():
    found, sources = contacts.collect(
        "Canal de finanças.",
        ["Links: linktr.ee/anafinancas\nOferecimento: corretoraxp.com.br", "Todos os links: linktr.ee/anafinancas"],
        min_video_repeats=2,
    )
    assert found["link_in_bio"] == ["linktr.ee/anafinancas"]
    assert not found["websites"]
    assert sources == [("link_in_bio", "linktr.ee/anafinancas", "descrição de 2 vídeo(s) recente(s)")]


# ---------------------------------------------------------------------- perfis
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("@canal", ("handle", "canal")),
        ("https://www.youtube.com/@Canal_X/videos", ("handle", "Canal_X")),
        ("youtube.com/channel/" + cid(3), ("id", cid(3))),
        (cid(3), ("id", cid(3))),
        ("https://youtube.com/user/fulano", ("username", "fulano")),
        ("https://www.youtube.com/c/Fulano", ("name", "Fulano")),
        ("https://youtu.be/abcdefghijk", ("video", "abcdefghijk")),
        ("https://www.youtube.com/watch?v=abcdefghijk&t=1", ("video", "abcdefghijk")),
        ("https://www.youtube.com/shorts/abcdefghijk", ("video", "abcdefghijk")),
        ("https://instagram.com/x", None),
        ("", None),
        ("Nome Com Espaço", None),
    ],
)
def test_parse_profile(raw, expected):
    assert parse_profile(raw) == expected


# ---------------------------------------------------------------- configuração
def test_config_validation_messages():
    with pytest.raises(ValueError, match="nicho ou um perfil"):
        SearchConfig().validate()
    with pytest.raises(ValueError, match="mínimo de inscritos"):
        SearchConfig(niches=["x"], min_subscribers=10, max_subscribers=5).validate()
    with pytest.raises(ValueError, match="extração de contatos"):
        SearchConfig(niches=["x"], only_with_contact=True).validate()


def test_config_normalizes_input_and_estimates_quota():
    cfg = SearchConfig(
        niches=["  finanças   pessoais ", "finanças pessoais", ""],
        pages_per_niche=50,
        recent_videos=0,
        active_within_days=30,
        region="br",
        min_subscribers=0,
    )
    assert cfg.niches == ["finanças pessoais"]
    assert cfg.pages_per_niche == 10 and cfg.region == "BR" and cfg.min_subscribers is None
    assert cfg.videos_per_channel == 1  # o filtro de atividade precisa do vídeo mais recente
    assert cfg.estimate()["search_calls"] == 10


# ------------------------------------------------------------------- pipeline
def test_main_scenario_filters_and_sorts(result):
    assert titles(result) == MAIN_TITLES
    stats = result.stats
    assert stats["filtered"] == {"subscribers_out_of_range": 1, "subscribers_hidden": 1, "inactive": 1}
    assert stats["unresolved_profiles"] == ["@naoexiste"]
    assert stats["usage_this_run"] == {"search": 3, "units": 8}
    assert stats["with_email"] == 3 and stats["with_direct_contact"] == 3
    assert stats["interrupted"] is False and stats["warnings"] == []


def test_record_has_profile_metrics_and_contacts(result):
    ana = result.records[0]
    assert ana["channel_id"] == cid(1)
    assert ana["found_by"] == [NICHE_A, NICHE_B] and ana["search_hits"] == 5 and ana["source"] == "busca"
    assert ana["url"] == "https://www.youtube.com/@anafinancas"
    assert ana["about_url"] == "https://www.youtube.com/@anafinancas/about"
    assert ana["recent_videos_analyzed"] == 3
    assert ana["avg_views_recent"] == pytest.approx(33333.3, abs=0.1)
    assert ana["avg_comments_recent"] == 150  # vídeo com comentários fechados fica fora da média
    assert ana["engagement_rate_recent"] == pytest.approx(0.058)
    assert ana["days_since_last_upload"] == 5 and ana["avg_days_between_uploads"] == 14.0
    assert ana["keywords"] == ["finanças", "educação financeira", "investimentos"]
    assert ana["primary_email"] == "contato@anafinancas.com.br"
    assert ana["contacts"]["link_in_bio"] == ["linktr.ee/anafinancas"]
    assert "corretoraxp" not in str(ana["contacts"])  # patrocinador citado em um vídeo só

    direct = result.records[2]
    assert direct["source"] == "perfil informado" and direct["found_by"] == ["perfil informado"]
    assert direct["subscribers"] == 5_000  # perfil informado ignora a faixa de inscritos
    assert direct["contacts"]["whatsapp"] == ["+5521998765432"]


def test_contact_rows_keep_where_each_contact_was_found(result):
    rows = {(row["title"], row["type"]): row["origin"] for row in result.contact_rows}
    assert rows[("Ana Finanças", "emails")] == "descrição do canal"
    assert rows[("Ana Finanças", "link_in_bio")] == "descrição de 2 vídeo(s) recente(s)"
    assert rows[("Investe Já", "tiktok")] == "descrição de 2 vídeo(s) recente(s)"


def test_cache_avoids_spending_quota_again(make_client, fake):
    run(make_client(), main_config(), now=NOW)
    calls = fake.count()
    again = run(make_client(), main_config(), now=NOW)
    assert fake.count() == calls
    assert again.stats["cache_hits"] == 11
    assert again.stats["usage_this_run"] == {"search": 0, "units": 0}
    assert titles(again) == MAIN_TITLES


def test_search_parameters_follow_the_config(make_client, fake):
    cfg = SearchConfig(niches=[NICHE_B], published_after="2026-01-01", order="date", region="pt", language="")
    run(make_client(), cfg, now=NOW)
    endpoint, params = fake.calls[0]
    assert endpoint == "search"
    assert params["publishedAfter"] == "2026-01-01T00:00:00Z"
    assert params["order"] == "date" and params["regionCode"] == "PT" and params["type"] == "video"
    assert "relevanceLanguage" not in params
    run(make_client(), SearchConfig(niches=[NICHE_B], mode="canais"), now=NOW)
    assert [p["type"] for e, p in fake.calls if e == "search"][-1] == "channel"


def test_profiles_bypass_every_filter(make_client):
    cfg = SearchConfig(
        profiles=["https://www.youtube.com/@semvideos", "youtube.com/channel/" + cid(3)],
        min_subscribers=100_000,
        active_within_days=30,
        extract_contacts=True,
        only_with_contact=True,
    )
    result = run(make_client(), cfg, now=NOW)
    assert titles(result) == ["Canal Parado", "Sem Vídeos"]
    no_videos = result.records[1]
    assert no_videos["recent_videos_analyzed"] == 0 and no_videos["days_since_last_upload"] is None


# ------------------------------------------------------------ cota e erros
def test_local_search_limit_returns_partial_result(make_client, fake):
    result = run(make_client(search_calls_per_day=2), main_config(), now=NOW)
    assert fake.count("search") == 2
    assert result.stats["interrupted"] is True
    assert any("Busca interrompida" in warning and NICHE_B in warning for warning in result.stats["warnings"])
    assert titles(result) == ["Ana Finanças", "Canal Direto"]


def test_google_quota_on_videos_keeps_channels_and_skips_activity_filter(make_client, fake):
    fake.fail_always["videos"] = (403, "quotaExceeded")
    result = run(make_client(), main_config(), now=NOW)
    assert titles(result) == ["Ana Finanças", "Investe Já", "Canal Parado", "Canal Direto"]
    assert result.stats["interrupted"] is True
    assert any("filtro de atividade não foi aplicado" in warning for warning in result.stats["warnings"])
    assert all(record["recent_videos_analyzed"] == 0 for record in result.records)


def test_invalid_key_raises_friendly_error(make_client, fake):
    fake.fail_always["search"] = (400, "API_KEY_INVALID")
    with pytest.raises(YouTubeAPIError, match="Chave de API inválida"):
        run(make_client(), main_config(), now=NOW)


def test_transient_error_is_retried(make_client, fake):
    fake.fail_once["channels"] = [(503, "backendError")]
    result = run(make_client(), main_config(), now=NOW)
    assert titles(result) == MAIN_TITLES
    assert result.stats["usage_this_run"]["units"] == 9  # a tentativa que falhou também conta na cota
