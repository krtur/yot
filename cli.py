#!/usr/bin/env python3
"""YT Prospector na linha de comando.

Exemplos:
  python cli.py -n "finanças pessoais" -n "investimentos" --contatos
  python cli.py --arquivo-nichos nichos.txt --paginas 3 --min-inscritos 10k --max-inscritos 500k --ativo-dias 90
  python cli.py -p @canal -p https://www.youtube.com/@outro --contatos --formatos xlsx,csv
  python cli.py -n "receitas veganas" --paginas 5 --estimar
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable, TextIO

import dotenv

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prospector import __version__, export, pipeline, supabase_sink  # noqa: E402
from prospector.youtube import (  # noqa: E402
    DEFAULT_SEARCH_CALLS_PER_DAY,
    DEFAULT_UNITS_PER_DAY,
    YouTubeAPIError,
    YouTubeClient,
)

ORDER_CHOICES = {"relevancia": "relevance", "data": "date", "visualizacoes": "viewCount", "avaliacao": "rating"}
FORMATS = ("xlsx", "csv", "json")
DEFAULT_CACHE = ROOT / ".cache" / "youtube.sqlite"
SUMMARY_SECTIONS = ("Resultado", "Filtrados", "Avisos", "Cota")
_MULTIPLIERS = {
    "": 1, "k": 1_000, "mil": 1_000,
    "m": 1_000_000, "mi": 1_000_000, "mm": 1_000_000,
    "milhao": 1_000_000, "milhão": 1_000_000, "milhoes": 1_000_000, "milhões": 1_000_000,
}
_WRITERS: dict[str, Callable[[pipeline.RunResult, Path], Path]] = {
    "xlsx": export.write_xlsx,
    "csv": export.write_csv,
    "json": export.write_json,
}


# ------------------------------------------------------------------ conversões
def parse_count(text: str) -> int:
    """Aceita 10000, 10k, 10 mil, 1,5M, 2.500.000 ou 25,000."""
    raw = str(text).strip().lower().replace(" ", "")
    match = re.fullmatch(r"(\d+(?:[.,]\d+)*)([a-zãõ]*)", raw)
    if not match or match.group(2) not in _MULTIPLIERS:
        raise argparse.ArgumentTypeError(f"número inválido: {text!r} (exemplos: 5000, 10k, 10mil, 1,5M, 25.000)")
    digits, suffix = match.groups()
    if not suffix and re.fullmatch(r"\d{1,3}(?:([.,])\d{3})(?:\1\d{3})*", digits):
        value = float(re.sub(r"[.,]", "", digits))  # separador de milhar
    elif re.fullmatch(r"\d+(?:[.,]\d+)?", digits):
        value = float(digits.replace(",", "."))  # decimal: 1,5M
    else:
        raise argparse.ArgumentTypeError(f"número inválido: {text!r} (exemplos: 5000, 10k, 10mil, 1,5M, 25.000)")
    return int(round(value * _MULTIPLIERS[suffix]))


def parse_date(text: str) -> date:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"data inválida: {text!r} (use AAAA-MM-DD ou DD/MM/AAAA)")


def parse_region(text: str) -> str | None:
    value = text.strip()
    if value.lower() in ("", "todas", "todos", "all"):
        return None
    if not re.fullmatch(r"[A-Za-z]{2}", value):
        raise argparse.ArgumentTypeError(f"região inválida: {text!r} (use o código de 2 letras do país, ex.: BR)")
    return value.upper()


def parse_language(text: str) -> str | None:
    value = text.strip()
    if value.lower() in ("", "todas", "todos", "all"):
        return None
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?", value):
        raise argparse.ArgumentTypeError(f"idioma inválido: {text!r} (ex.: pt, en, es)")
    return value


def parse_formats(text: str) -> tuple[str, ...]:
    chosen = [item.strip().lower().lstrip(".") for item in text.split(",") if item.strip()]
    invalid = [item for item in chosen if item not in FORMATS]
    if not chosen or invalid:
        raise argparse.ArgumentTypeError(f"formatos inválidos: {text!r} (use {', '.join(FORMATS)})")
    return tuple(dict.fromkeys(chosen))


def int_range(minimum: int, maximum: int) -> Callable[[str], int]:
    def convert(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"número inteiro inválido: {text!r}") from None
        if not minimum <= value <= maximum:
            raise argparse.ArgumentTypeError(f"use um valor entre {minimum} e {maximum}")
        return value

    return convert


def read_lines(path: str | None) -> list[str]:
    """Um item por linha; ignora linhas vazias e comentários (#)."""
    if not path:
        return []
    text = Path(path).read_text(encoding="utf-8-sig")
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def _env_int(name: str, default: int) -> int:
    try:
        return max(int(os.getenv(name, "")), 1)
    except ValueError:
        return default


# ---------------------------------------------------------------------- parser
class _HelpFormatter(argparse.HelpFormatter):
    def add_usage(self, usage, actions, groups, prefix=None):  # type: ignore[no-untyped-def]
        return super().add_usage(usage, actions, groups, "uso: " if prefix is None else prefix)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yt-prospector",
        description="Descobre canais do YouTube por nicho, pela API oficial, e exporta os dados de cada perfil.",
        epilog='Exemplo: python cli.py -n "finanças pessoais" -n investimentos --min-inscritos 10k --contatos',
        formatter_class=_HelpFormatter,
        add_help=False,
    )
    general = parser.add_argument_group("geral")
    general.add_argument("-h", "--ajuda", action="help", help="mostra esta ajuda e sai")
    general.add_argument("--versao", action="version", version=f"%(prog)s {__version__}",
                         help="mostra a versão e sai")
    search = parser.add_argument_group("o que buscar")
    search.add_argument("-n", "--nicho", action="append", default=[], metavar="TERMO",
                        help="nicho ou palavra-chave (repita para vários)")
    search.add_argument("--arquivo-nichos", metavar="ARQUIVO", help="arquivo .txt com um nicho por linha")
    search.add_argument("-p", "--perfil", action="append", default=[], metavar="PERFIL",
                        help="@handle, link do canal, link de um vídeo ou ID UC... (repita para vários)")
    search.add_argument("--arquivo-perfis", metavar="ARQUIVO", help="arquivo .txt com um perfil por linha")
    search.add_argument("--modo", choices=pipeline.MODES, default="videos",
                        help="videos: canais que publicam sobre o nicho (padrão); canais: canais cujo nome ou "
                             "descrição batem com o termo")
    search.add_argument("--paginas", type=int_range(1, pipeline.MAX_PAGES_PER_NICHE), default=1, metavar="N",
                        help="páginas de até 50 resultados por nicho; cada página gasta 1 busca da cota diária "
                             "(padrão: 1)")
    search.add_argument("--regiao", type=parse_region, default="BR", metavar="PAÍS",
                        help="código do país, ex.: BR, PT; 'todas' para não filtrar (padrão: BR)")
    search.add_argument("--idioma", type=parse_language, default="pt", metavar="IDIOMA",
                        help="idioma preferido dos resultados, ex.: pt, en; 'todos' para não filtrar (padrão: pt)")
    search.add_argument("--ordem", choices=ORDER_CHOICES, default="relevancia",
                        help="ordenação da busca (padrão: relevancia)")
    search.add_argument("--publicado-apos", type=parse_date, metavar="DATA",
                        help="só resultados publicados depois da data (AAAA-MM-DD ou DD/MM/AAAA)")

    filters = parser.add_argument_group("filtros e contatos")
    filters.add_argument("--min-inscritos", type=parse_count, metavar="N", help="ex.: 10000, 10k, 10mil")
    filters.add_argument("--max-inscritos", type=parse_count, metavar="N", help="ex.: 500k, 1,5M")
    filters.add_argument("--ativo-dias", type=int_range(0, 3650), metavar="DIAS",
                         help="só canais com upload nos últimos N dias")
    filters.add_argument("--max-canais", type=int_range(0, 100_000), metavar="N",
                         help="teto de canais analisados; entram primeiro os que mais apareceram na busca")
    filters.add_argument("--videos-recentes", type=int_range(0, pipeline.MAX_RECENT_VIDEOS), default=10,
                         metavar="N", help="vídeos recentes lidos por canal, base das médias (padrão: 10)")
    filters.add_argument("--contatos", action="store_true",
                         help="extrai contatos publicados pelo criador (descrição do canal e links repetidos "
                              "nos vídeos recentes)")
    filters.add_argument("--somente-com-contato", action="store_true",
                         help="mantém só canais com algum contato publicado (exige --contatos)")

    output = parser.add_argument_group("saída")
    output.add_argument("--saida", default="saida", metavar="PASTA", help="pasta dos arquivos (padrão: ./saida)")
    output.add_argument("--formatos", type=parse_formats, default=("xlsx", "json"), metavar="LISTA",
                        help="xlsx, csv e/ou json, separados por vírgula (padrão: xlsx,json)")
    output.add_argument("--supabase", action="store_true",
                        help="também grava os canais no Supabase (SUPABASE_URL e SUPABASE_SECRET_KEY no .env)")

    quota = parser.add_argument_group("cota e cache")
    quota.add_argument("--estimar", action="store_true",
                       help="mostra o teto de consumo de cota e sai, sem chamar a API")
    quota.add_argument("--max-buscas-dia", type=int_range(1, 10_000_000), metavar="N",
                       default=_env_int("YTP_MAX_BUSCAS_DIA", DEFAULT_SEARCH_CALLS_PER_DAY),
                       help=f"limite local de buscas por dia (padrão: {DEFAULT_SEARCH_CALLS_PER_DAY})")
    quota.add_argument("--max-unidades-dia", type=int_range(1, 100_000_000), metavar="N",
                       default=_env_int("YTP_MAX_UNIDADES_DIA", DEFAULT_UNITS_PER_DAY),
                       help=f"limite local de unidades por dia (padrão: {DEFAULT_UNITS_PER_DAY})")
    quota.add_argument("--cache", default=os.getenv("YTP_CACHE") or str(DEFAULT_CACHE), metavar="ARQUIVO",
                       help="arquivo SQLite do cache e do contador de cota")
    quota.add_argument("--cache-horas", type=float, default=24, metavar="H",
                       help="validade do cache em horas; 0 desliga (padrão: 24)")
    return parser


# -------------------------------------------------------------------- execução
class ProgressPrinter:
    """Progresso no terminal: atualiza a mesma linha no TTY, uma linha por etapa fora dele."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stderr
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self._stage: str | None = None
        self._open = False

    def __call__(self, stage: str, done: int, total: int) -> None:
        label = pipeline.STAGE_LABELS.get(stage, stage)
        if self.tty:
            self.stream.write(f"\r\033[K{label}: {done}/{total}")
            self._open = True
        elif stage != self._stage:
            self.stream.write(f"{label}...\n")
        self._stage = stage
        self.stream.flush()

    def close(self) -> None:
        if self._open:
            self.stream.write("\n")
            self.stream.flush()
            self._open = False


def write_outputs(result: pipeline.RunResult, folder: Path, formats: tuple[str, ...]) -> list[Path]:
    base = export.default_basename(result)
    return [_WRITERS[fmt](result, folder / f"{base}.{fmt}") for fmt in formats]


def print_estimate(cfg: pipeline.SearchConfig, args: argparse.Namespace, out: TextIO) -> None:
    estimate = cfg.estimate()
    fmt = export.format_int
    out.write(
        "Estimativa (teto, sem chamar a API)\n"
        f"  Buscas: até {fmt(estimate['search_calls'])} de {fmt(args.max_buscas_dia)} por dia\n"
        f"  Canais avaliados: até {fmt(estimate['max_channels'])}\n"
        f"  Unidades: até {fmt(estimate['max_units'])} de {fmt(args.max_unidades_dia)} por dia\n"
        "O consumo real costuma ser menor: resultados repetidos e o cache economizam cota.\n"
    )


def print_summary(
    result: pipeline.RunResult,
    files: list[Path],
    out: TextIO,
    *,
    supabase_sent: int | None = None,
    supabase_table: str = "",
    supabase_error: str | None = None,
) -> None:
    stats = result.stats
    out.write(f"\nPronto: {export.format_int(stats['channels_in_report'])} canais no relatório.\n")
    if not result.records:
        out.write("Nenhum canal passou pelos filtros. Tente afrouxar a faixa de inscritos ou o período de atividade.\n")
    current = None
    for section, label, value in export.summary_items(result):
        if section not in SUMMARY_SECTIONS:
            continue
        if section != current:
            out.write(f"\n{section}\n")
            current = section
        out.write(f"  - {value}\n" if section == "Avisos" else f"  {label}: {value}\n")
    out.write("\nArquivos\n")
    for path in files:
        out.write(f"  {path}\n")
    if supabase_error:
        out.write(f"\nSupabase: {supabase_error}\n")
    elif supabase_sent is not None:
        out.write(f"\nSupabase: {export.format_int(supabase_sent)} canais gravados na tabela {supabase_table}.\n")
    out.write(f"\n{export.SOURCE_NOTE} {export.RETENTION_NOTE}\n")


def main(argv: list[str] | None = None) -> int:
    _tolerant_console()
    dotenv.load_dotenv(ROOT / ".env")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        niches = [*args.nicho, *read_lines(args.arquivo_nichos)]
        profiles = [*args.perfil, *read_lines(args.arquivo_perfis)]
    except OSError as exc:
        parser.error(f"não consegui ler o arquivo {exc.filename}: {exc.strerror}")
    if args.somente_com_contato and not args.contatos:
        parser.error("--somente-com-contato exige --contatos")
    cfg = pipeline.SearchConfig(
        niches=niches,
        profiles=profiles,
        mode=args.modo,
        pages_per_niche=args.paginas,
        region=args.regiao,
        language=args.idioma,
        order=ORDER_CHOICES[args.ordem],
        published_after=args.publicado_apos,
        min_subscribers=args.min_inscritos,
        max_subscribers=args.max_inscritos,
        active_within_days=args.ativo_dias,
        recent_videos=args.videos_recentes,
        extract_contacts=args.contatos,
        only_with_contact=args.somente_com_contato,
        max_channels=args.max_canais,
    )
    try:
        cfg.validate()
    except ValueError as exc:
        parser.error(str(exc))
    if args.estimar:
        print_estimate(cfg, args, sys.stdout)
        return 0

    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        print("Falta a chave da API: defina YOUTUBE_API_KEY no arquivo .env (modelo em .env.example).",
              file=sys.stderr)
        return 1
    url, key, table = supabase_sink.settings()
    if args.supabase and not (url and key):
        print("Para usar --supabase, defina SUPABASE_URL e SUPABASE_SECRET_KEY no arquivo .env.", file=sys.stderr)
        return 1

    progress = ProgressPrinter()
    try:
        client = YouTubeClient(
            api_key,
            cache_path=args.cache,
            cache_ttl_hours=args.cache_horas,
            search_calls_per_day=args.max_buscas_dia,
            units_per_day=args.max_unidades_dia,
        )
        result = pipeline.run(client, cfg, progress)
    except YouTubeAPIError as exc:
        progress.close()
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    progress.close()

    files = write_outputs(result, Path(args.saida), args.formatos)
    sent: int | None = None
    error: str | None = None
    if args.supabase:
        try:
            sent = supabase_sink.upsert(result, url, key, table)
        except supabase_sink.SupabaseError as exc:
            error = str(exc)
    print_summary(result, files, sys.stdout, supabase_sent=sent, supabase_table=table, supabase_error=error)
    return 1 if error else 0


def _tolerant_console() -> None:
    """Evita que emoji ou alfabetos diferentes em nomes de canais derrubem a saída em consoles antigos."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, io.UnsupportedOperation):
            pass


if __name__ == "__main__":
    raise SystemExit(main())
