from __future__ import annotations

import pytest
import requests

from fakes import NOW, FakeYouTube, main_config
from prospector.pipeline import run
from prospector.youtube import YouTubeClient


@pytest.fixture(autouse=True)
def _no_real_env(monkeypatch):
    """Nenhum teste depende de .env, chaves reais ou Supabase configurado na máquina."""
    for name in ("YOUTUBE_API_KEY", "SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_TABLE",
                 "YTP_CACHE", "YTP_MAX_BUSCAS_DIA", "YTP_MAX_UNIDADES_DIA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)


@pytest.fixture
def fake() -> FakeYouTube:
    return FakeYouTube()


@pytest.fixture
def make_client(tmp_path, fake):
    def factory(**kwargs) -> YouTubeClient:
        kwargs.setdefault("cache_path", tmp_path / "cache.sqlite")
        client = YouTubeClient("chave-de-teste", session=fake, **kwargs)
        client._sleep = lambda seconds: None
        return client

    return factory


@pytest.fixture
def result(make_client):
    return run(make_client(), main_config(), now=NOW)


@pytest.fixture
def patched_requests(monkeypatch, fake, tmp_path) -> FakeYouTube:
    """requests.Session() passa a devolver a API falsa (usado pela CLI e pelo Streamlit)."""
    monkeypatch.setattr(requests, "Session", lambda: fake)
    monkeypatch.setenv("YOUTUBE_API_KEY", "chave-de-teste")
    monkeypatch.setenv("YTP_CACHE", str(tmp_path / "app-cache.sqlite"))
    return fake
