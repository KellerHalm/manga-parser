"""Use-case: полный обход каталога с чекпоинтом и выгрузкой.

Не знает ни про один источник поимённо — работает через порт Source.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.ports import Source
from ..infrastructure.storage import JsonlCheckpoint, finalize
from .report import Reporter


def dump_catalog(
    source: Source,
    out: Path,
    *,
    delay: float | None = None,
    max_pages: int | None = None,
    want_csv: bool = True,
    resume: bool = True,
    reporter: Reporter | None = None,
    **options: Any,
) -> dict[str, Any]:
    """
    Обходит каталог целиком и пишет JSON (+CSV/JSONL).

    Прогресс дописывается в `<out>.jsonl` по мере обхода, поэтому прерывание
    (Ctrl+C, краш, 429) не теряет собранное: повторный запуск продолжит с
    чекпоинта. `resume=False` начинает обход с чистого листа.
    """
    report = reporter or Reporter()
    out = Path(out)
    checkpoint_path = out.with_suffix(".jsonl")
    checkpoint = JsonlCheckpoint(checkpoint_path)
    if not resume and checkpoint_path.exists():
        checkpoint_path.unlink()

    done = checkpoint.load()
    report.info(f"[i] чекпоинт: {len(done)} записей -> {checkpoint_path}")

    fresh_rows: list[dict[str, Any]] = []
    try:
        for item in source.iter_catalog(
            delay=delay,
            max_pages=max_pages,
            checkpoint=checkpoint,
            on_page=report.on_page,
            **options,
        ):
            fresh_rows.append(item.flat())
    except KeyboardInterrupt:
        report.warn("\n[!] прервано пользователем — прогресс сохранён в чекпоинте")
    finally:
        checkpoint.close()

    written = finalize(
        [*done, *fresh_rows],
        out,
        key=getattr(source, "dedupe_key", "id"),
        want_csv=want_csv,
    )
    report.result(
        f"\n{out.name}: {written['rows']} записей за {report.elapsed():.1f} с\n"
        f"  JSON:  {written['json']}\n"
        f"  JSONL: {written['jsonl']}"
        + (f"\n  CSV:   {written['csv']}" if want_csv else "")
    )
    return written


def build_from_checkpoint(
    source: Source,
    out: Path,
    *,
    want_csv: bool = True,
    reporter: Reporter | None = None,
) -> dict[str, Any]:
    """Собирает JSON/CSV из чекпоинта без единого сетевого запроса."""
    report = reporter or Reporter()
    out = Path(out)
    rows = JsonlCheckpoint(out.with_suffix(".jsonl")).load()
    written = finalize(
        rows, out, key=getattr(source, "dedupe_key", "id"), want_csv=want_csv
    )
    report.result(f"{out.name}: {written['rows']} записей (из чекпоинта)")
    return written
