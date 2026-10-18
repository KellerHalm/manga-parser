"""
CLI: единая точка входа для всех парсеров.

    python main.py                                  # список источников и команд
    python main.py sources                          # только источники
    python main.py mangalib  search "наруто"
    python main.py remanga   dump --out data/remanga_catalog.json --csv
    python main.py inkstory  catalog --page 3
    python main.py all       search "берсерк"        # по всем источникам
    python main.py index     --catalog data/remanga_catalog.json
    python main.py merge     data/mangalib_catalog.json data/remanga_catalog.json

Команды одинаковы для всех источников (контракт `core.ports.Source`);
экзотические команды (latest, enrich) появляются только у источников,
которые их умеют.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..application import browse
from ..application import catalog as catalog_uc
from ..application import enrich as enrich_uc
from ..application import index as index_uc
from ..application import merge as merge_uc
from ..application.report import Reporter
from ..core.ports import default_out
from ..sources import ALL, REGISTRY, resolve_many

Handler = Callable[[Any, argparse.Namespace], int]

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INDEX = ROOT / "data" / "index.db"


# ---------- обработчики команд ----------


def cmd_search(args: argparse.Namespace, report: Reporter) -> int:
    for client in _clients(args.source):
        browse.search(
            client, args.query, page=args.page, limit=args.limit,
            reporter=report, **_source_options(client, args),
        )
    return 0


def cmd_catalog(args: argparse.Namespace, report: Reporter) -> int:
    for client in _clients(args.source):
        browse.catalog_page(
            client, page=args.page, limit=args.limit,
            reporter=report, **_source_options(client, args),
        )
    return 0


def cmd_details(args: argparse.Namespace, report: Reporter) -> int:
    for client in _clients(args.source):
        browse.details(client, browse.resolve_key(client, args.key, reporter=report), reporter=report)
    return 0


def cmd_chapters(args: argparse.Namespace, report: Reporter) -> int:
    for client in _clients(args.source):
        key = browse.resolve_key(client, args.key, reporter=report)
        browse.chapters(
            client, key, limit=args.limit, with_pages=args.pages, reporter=report,
        )
    return 0


def cmd_pages(args: argparse.Namespace, report: Reporter) -> int:
    """Адреса картинок главы — по номеру главы в списке или по её адресу."""
    for client in _clients(args.source):
        key = browse.resolve_key(client, args.key, reporter=report)
        chapter = {"url": args.chapter_url} if args.chapter_url else None
        browse.chapter_pages(
            client, key, chapter=chapter, index=args.index, limit=args.limit, reporter=report,
        )
    return 0


def cmd_latest(args: argparse.Namespace, report: Reporter) -> int:
    for client in _clients(args.source):
        items = client.latest(page=args.page, count=args.count)
        report.result(f"Новинки, стр. {args.page} [{client.name}]: {len(items)}\n")
        for item in items[: args.limit]:
            report.result(browse.format_row(item))
    return 0


def cmd_dump(args: argparse.Namespace, report: Reporter) -> int:
    for client in _clients(args.source):
        out = Path(args.out) if args.out else default_out(client.name, ROOT)
        catalog_uc.dump_catalog(
            client,
            out,
            delay=args.delay,
            max_pages=args.max_pages,
            want_csv=args.csv,
            resume=not args.fresh,
            reporter=report,
            **_source_options(client, args),
        )
    return 0


def cmd_build(args: argparse.Namespace, report: Reporter) -> int:
    for client in _clients(args.source):
        out = Path(args.out) if args.out else default_out(client.name, ROOT)
        catalog_uc.build_from_checkpoint(client, out, want_csv=args.csv, reporter=report)
    return 0


def cmd_enrich(args: argparse.Namespace, report: Reporter) -> int:
    """Дозагружает полные карточки тайтлов (все источники, у которых есть details())."""
    for client in _clients(args.source):
        out = Path(args.out) if args.out else default_out(client.name, ROOT)
        enrich_uc.enrich_catalog(
            client,
            out,
            limit=args.limit,
            delay=args.delay,
            want_csv=args.csv,
            refresh=args.refresh,
            reporter=report,
        )
    return 0


def cmd_index(args: argparse.Namespace, report: Reporter) -> int:
    catalogs = [Path(p) for p in args.catalog] or [
        default_out(name, ROOT) for name in REGISTRY
    ]
    index_uc.build_index(catalogs, args.out, reporter=report, reset=args.reset)
    return 0


def cmd_offline(args: argparse.Namespace, report: Reporter) -> int:
    index_uc.search_index(args.out, args.query, limit=args.limit, reporter=report)
    return 0


def cmd_merge(args: argparse.Namespace, report: Reporter) -> int:
    merge_uc.merge_catalogs(args.catalog, args.out, want_csv=not args.no_csv, reporter=report)
    return 0


def cmd_gui(args: argparse.Namespace, report: Reporter) -> int:
    """Графическое приложение. Требует `pip install flet`."""
    try:
        import flet as ft
    except ImportError:
        report.info(
            "Для графического приложения нужен Flet:\n"
            "    pip install -r requirements.txt      # или: pip install flet"
        )
        return 2

    from ..gui.app import main as gui_main

    view = ft.AppView.WEB_BROWSER if args.web else ft.AppView.FLET_APP
    ft.run(gui_main, host=args.host, port=args.port, view=view)
    return 0


def _clients(name: str) -> list[Any]:
    return [spec.build() for spec in resolve_many(name)]


def _source_options(client: Any, args: argparse.Namespace) -> dict[str, Any]:
    """Прокидывает --sort/--count только тем источникам, которые их понимают."""
    params = client.catalog.__code__.co_varnames
    options: dict[str, Any] = {}
    if (sort := getattr(args, "sort", None)) and "sort" in params:
        options["sort"] = sort
    if (count := getattr(args, "count", None)) and "count" in params:
        options["count"] = count
    return options


# ---------- описание команд ----------


def _add_search(p: argparse.ArgumentParser) -> None:
    p.add_argument("query", help="название тайтла")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--limit", type=int, default=25, help="сколько показать")
    p.add_argument("--sort", default=None, help="сортировка источника")


def _add_catalog(p: argparse.ArgumentParser) -> None:
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--count", type=int, default=None, help="размер страницы (remanga)")
    p.add_argument("--sort", default=None, help="сортировка источника")


def _add_key(p: argparse.ArgumentParser, what: str) -> None:
    p.add_argument("key", help=f"{what} тайтла в источнике (slug / dir)")


def _add_dump(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", default=None, help="путь JSON (по умолчанию data/<source>_catalog.json)")
    p.add_argument("--csv", action="store_true", help="дополнительно CSV")
    p.add_argument("--delay", type=float, default=None, help="пауза между запросами, сек")
    p.add_argument("--max-pages", type=int, default=None, help="ограничить число страниц")
    p.add_argument("--fresh", action="store_true", help="игнорировать чекпоинт и начать заново")
    p.add_argument("--sort", default=None, help="сортировка источника")


def _add_chapters(p: argparse.ArgumentParser) -> None:
    _add_key(p, "slug/dir")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument(
        "--pages",
        action="store_true",
        help="дозагрузить число страниц каждой главы (запрос на главу)",
    )


def _add_pages(p: argparse.ArgumentParser) -> None:
    _add_key(p, "slug/dir")
    p.add_argument(
        "--chapter",
        dest="index",
        type=int,
        default=0,
        help="номер главы в списке, с нуля (по умолчанию первая)",
    )
    p.add_argument(
        "--url",
        dest="chapter_url",
        default=None,
        help="адрес главы напрямую (когда список глав недоступен)",
    )
    p.add_argument("--limit", type=int, default=0, help="сколько адресов показать (0 — все)")


#: (имя команды, справка, обработчик, builder) — команды добавляются в цикле.
COMMANDS: list[tuple[str, str, Handler, Callable[[argparse.ArgumentParser], None]]] = [
    ("search", "поиск по названию (серверный)", cmd_search, _add_search),
    ("catalog", "одна страница каталога", cmd_catalog, _add_catalog),
    ("latest", "лента новинок (remanga)", cmd_latest, _add_catalog),
    ("details", "карточка тайтла", cmd_details, lambda p: _add_key(p, "slug/dir")),
    ("chapters", "список глав", cmd_chapters, _add_chapters),
    ("pages", "адреса страниц главы", cmd_pages, _add_pages),
    ("dump", "выгрузить весь каталог (с чекпоинтом)", cmd_dump, _add_dump),
    ("build", "собрать JSON/CSV из чекпоинта, без сети", cmd_build, lambda p: (
        p.add_argument("--out", default=None),
        p.add_argument("--csv", action="store_true"),
    )),
    ("enrich", "дозагрузить полные карточки (описание, жанры, авторы)", cmd_enrich, lambda p: (
        p.add_argument("--out", default=None),
        p.add_argument("--limit", type=int, default=100, help="сколько карточек (0 — без ограничения)"),
        p.add_argument("--delay", type=float, default=None, help="пауза между запросами, сек"),
        p.add_argument("--csv", action="store_true", help="пересобрать CSV"),
        p.add_argument("--refresh", action="store_true", help="обогатить заново уже обогащённые"),
    )),
]


#: Флаги, после которых идёт значение: их нельзя принять за имя источника.
VALUE_FLAGS = {"--out", "--catalog", "--index", "--query", "-o"}


def command_names() -> set[str]:
    """Все имена команд CLI, включая глобальные."""
    return {name for name, *_ in COMMANDS} | {"sources", "index", "offline", "merge", "gui"}


def split_source(argv: Sequence[str]) -> tuple[str | None, list[str]]:
    """
    Вытаскивает имя источника из начала командной строки.

    argparse не умеет позиционный аргумент перед подкомандами, поэтому
    источник (`mangalib`) отделяется вручную: `main.py mangalib search ...`.
    Разбор идёт до первого имени команды, поэтому `main.py search remanga`
    ищет по слову «remanga», а не выбирает источник.
    """
    args = list(argv)
    names = command_names()
    skip_value = False
    for index, token in enumerate(args):
        if skip_value:
            skip_value = False
            continue
        if token in names:
            break
        if token.startswith("-"):
            skip_value = token in VALUE_FLAGS
            continue
        if token == ALL or token in REGISTRY:
            return token, args[:index] + args[index + 1:]
    return None, args


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Парсеры каталогов манги (MangaLIB, ReManga, InkStory, BuZuManga, Com-X, Desu.Me, SelfManga)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Формат:  python main.py <источник> <команда> [аргументы]\n\n"
            "Примеры:\n"
            "  python main.py sources\n"
            "  python main.py mangalib search \"берсерк\"\n"
            "  python main.py remanga  dump --csv --max-pages 5\n"
            "  python main.py all     search naruto\n"
            "  python main.py index --catalog data/remanga_catalog.json\n"
            "  python main.py offline \"берсерк\"\n"
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="только результаты, без прогресса")

    sub = parser.add_subparsers(dest="cmd", required=True, metavar="команда")

    # sources обрабатывается в main(): он не требует источника и не ходит в сеть
    sub.add_parser("sources", help="список источников").set_defaults(needs_source=False)

    for name, help_text, handler, builder in COMMANDS:
        cmd = sub.add_parser(name, help=help_text, description=help_text)
        builder(cmd)
        cmd.set_defaults(handler=handler, needs_source=True)

    idx = sub.add_parser("index", help="построить офлайн-индекс из выгрузок")
    idx.add_argument(
        "--catalog",
        action="extend",
        nargs="+",
        default=[],
        help="файлы выгрузок (по умолчанию все из data/)",
    )
    idx.add_argument("--out", type=Path, default=DEFAULT_INDEX)
    idx.add_argument("--reset", action="store_true", help="пересоздать индекс с нуля")
    idx.set_defaults(handler=cmd_index, needs_source=False)

    off = sub.add_parser("offline", help="поиск по офлайн-индексу")
    off.add_argument("query")
    off.add_argument("--out", type=Path, default=DEFAULT_INDEX)
    off.add_argument("--limit", type=int, default=30)
    off.set_defaults(handler=cmd_offline, needs_source=False)

    mg = sub.add_parser("merge", help="склеить выгрузки разных источников")
    mg.add_argument("catalog", nargs="+", type=Path)
    mg.add_argument("--out", type=Path, default=ROOT / "data" / "all_catalog.json")
    mg.add_argument("--no-csv", action="store_true")
    mg.set_defaults(handler=cmd_merge, needs_source=False)

    gui = sub.add_parser("gui", help="графическое приложение (Flet: Android / iOS / десктоп)")
    gui.add_argument("--host", default=None, help="хост веб-сервера для отладки в браузере")
    gui.add_argument("--port", type=int, default=0, help="порт веб-сервера")
    gui.add_argument("--web", action="store_true", help="открыть в браузере вместо окна")
    gui.set_defaults(handler=cmd_gui, needs_source=False)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from .render import print_sources

    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw:  # без аргументов показываем список источников и команд
        print_sources(REGISTRY)
        return 0

    source, rest = split_source(raw)

    first = next((token for token in rest if not token.startswith("-")), None)
    if first is not None and first not in command_names():
        print(
            f"Неизвестный источник или команда: {first}\n"
            f"Источники: {', '.join(REGISTRY)}, {ALL}\n"
            f"Команды: python main.py sources",
            file=sys.stderr,
        )
        return 2

    parser = build_parser()
    args = parser.parse_args(rest)
    args.source = source

    if args.needs_source and not source:
        print(
            f"Не указан источник. Формат: python main.py <источник> {args.cmd} ...\n"
            f"Источники: {', '.join(REGISTRY)}, {ALL}. Список команд: python main.py sources",
            file=sys.stderr,
        )
        return 2

    if args.cmd == "sources":
        print_sources(REGISTRY)
        return 0

    report = Reporter(quiet=args.quiet)
    try:
        return int(args.handler(args, report) or 0)
    except KeyError as exc:  # неизвестный источник
        print(str(exc).strip("'"), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n[!] прервано", file=sys.stderr)
        return 130
    except Exception as exc:  # единая точка для ошибок парсеров
        print(f"[!] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
