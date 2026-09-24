from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import cli
from fakes import NICHE_A, NICHE_B

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")


# ------------------------------------------------------------------------- CLI
@pytest.mark.parametrize(
    "text, expected",
    [("10000", 10_000), ("10k", 10_000), ("10 mil", 10_000), ("1,5M", 1_500_000), ("1.5m", 1_500_000),
     ("25.000", 25_000), ("2.500.000", 2_500_000), ("25,000", 25_000), ("2 milhões", 2_000_000)],
)
def test_parse_count_accepts_brazilian_shortcuts(text, expected):
    assert cli.parse_count(text) == expected


@pytest.mark.parametrize("text", ["abc", "10x", "-5", "", "1.2.3,4"])
def test_parse_count_rejects_garbage(text):
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_count(text)


def test_small_parsers():
    assert cli.parse_date("31/12/2025").isoformat() == "2025-12-31"
    assert cli.parse_date("2025-12-31").isoformat() == "2025-12-31"
    assert cli.parse_region("todas") is None and cli.parse_region("pt") == "PT"
    assert cli.parse_language("todos") is None and cli.parse_language("pt-BR") == "pt-BR"
    assert cli.parse_formats("xlsx, CSV,xlsx") == ("xlsx", "csv")
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_formats("pdf")


def test_estimate_runs_without_key(capsys):
    assert cli.main(["-n", NICHE_A, "-n", NICHE_B, "--paginas", "3", "--estimar"]) == 0
    out = capsys.readouterr().out
    assert "Buscas: até 6 de 100 por dia" in out


def test_missing_key_is_reported(capsys, tmp_path):
    assert cli.main(["-n", NICHE_A, "--saida", str(tmp_path)]) == 1
    assert "YOUTUBE_API_KEY" in capsys.readouterr().err


def test_only_with_contact_requires_contacts(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["-n", NICHE_A, "--somente-com-contato"])
    assert exit_info.value.code == 2
    assert "--contatos" in capsys.readouterr().err


def test_supabase_flag_requires_settings(patched_requests, capsys):
    assert cli.main(["-n", NICHE_A, "--supabase"]) == 1
    assert "SUPABASE_URL" in capsys.readouterr().err
    assert patched_requests.count() == 0


def test_cli_end_to_end(patched_requests, tmp_path, capsys):
    niches_file = tmp_path / "nichos.txt"
    niches_file.write_text(f"# comentário\n{NICHE_A}\n\n{NICHE_B}\n", encoding="utf-8")
    output = tmp_path / "saida"
    code = cli.main([
        "--arquivo-nichos", str(niches_file), "-p", "@canaldireto", "-p", "@naoexiste",
        "--paginas", "2", "--min-inscritos", "10k", "--max-inscritos", "1M", "--ativo-dias", "180",
        "--contatos", "--saida", str(output), "--formatos", "xlsx,csv,json",
    ])
    assert code == 0
    assert sorted(path.suffix for path in output.iterdir()) == [".csv", ".json", ".xlsx"]
    payload = json.loads(next(output.glob("*.json")).read_text(encoding="utf-8"))
    assert [channel["title"] for channel in payload["channels"]] == ["Ana Finanças", "Investe Já", "Canal Direto"]
    out = capsys.readouterr().out
    assert "Pronto: 3 canais no relatório." in out
    assert "@naoexiste" in out and "Buscas nesta execução: 3" in out
    assert patched_requests.count("search") == 3


def test_progress_printer_without_tty_prints_one_line_per_stage():
    stream = io.StringIO()
    printer = cli.ProgressPrinter(stream)
    for done in (1, 2):
        printer("busca", done, 2)
    printer("canais", 1, 1)
    printer.close()
    assert stream.getvalue().splitlines() == ["Buscando nos nichos...", "Carregando dados dos canais..."]


# ------------------------------------------------------------------- Streamlit
def test_app_without_key_explains_setup():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("YOUTUBE_API_KEY" in info.value for info in at.info)
    assert at.button(key="run").disabled


def test_app_runs_a_search_and_shows_tabs(patched_requests):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_area(key="niches").input(f"{NICHE_A}\n{NICHE_B}")
    at.text_area(key="profiles").input("@canaldireto")
    at.checkbox(key="extract").check()
    at.run()
    at.button(key="run").click().run()
    assert not at.exception
    result = at.session_state["result"]
    assert [record["title"] for record in result.records] == [
        "Mega Finanças", "Ana Finanças", "Investe Já", "Canal Parado", "Canal Direto",
    ]
    assert [tab.label for tab in at.tabs] == ["Canais", "Contatos", "Nichos", "Resumo"]
    assert patched_requests.count("search") == 2


def test_app_shows_validation_error_without_input(patched_requests):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.button(key="run").click().run()
    assert not at.exception
    assert any("nicho ou um perfil" in error.value for error in at.error)
    assert patched_requests.count() == 0
