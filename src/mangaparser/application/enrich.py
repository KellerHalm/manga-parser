"""Use-case: обогащение выгрузки полными карточками тайтлов.

Каталог отдаёт срез — название, обложка, рейтинг, а полная карточка — описание,
жанры, авторов, издателей. Здесь эти два набора соединяются: для каждой записи
чекпоинта вызывается `Source.details()` и непустые поля карточки досыпаются
в запись выгрузки.

Обогащение универсально: достаточно, чтобы источник умел `details()`. Значит
работает одинаково для MangaLIB, ReManga, InkStory и BuZuManga, и новый
источник получает её бесплатно.

Операция медленная — один запрос на тайтл, поэтому:

    * ограничена `--limit` (`0` — без ограничения);
    * прогресс дописывается в чекпоинт, краш не теряет собранное;
    * обогащённая запись помечается полем `details_at`, поэтому повторный
      запуск продолжает с места остановки, а не начинает по новой.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core.http import SourceError, Throttle
from ..core.models import merge_details
from ..core.ports import Source
from ..infrastructure.storage import finalize, write_jsonl
from .index import iter_rows
from .report import Reporter

#: Поле-метка «карточка уже загружена» в записи выгрузки.
DETAILS_MARK = "details_at"

#: Как часто перезаписывать чекпоинт, чтобы краш не съел больше этого числа карточек.
FLUSH_EVERY = 25


def row_key(source: Source, row: dict[str, Any]) -> str:
    """
    Ключ, по которому источник адресует карточку.

    Берётся поле, названное источником в `key_hint` (у ReManga это `dir`,
    у MangaLIB — `slug`), иначе любой идентификатор записи. Ввод пользователя
    тут не участвует: обогащается уже выгруженный каталог.
    """
    hint = getattr(source, "key_hint", None)
    candidates = (row.get(hint) if hint else None, row.get("slug"), row.get("id"), row.get("url"))
    for value in candidates:
        if value not in (None, ""):
            return str(value)
    return ""


def flat_of(item: Any) -> dict[str, Any]:
    """Плоская запись источника — из объекта или из готового словаря."""
    if isinstance(item, dict):
        return dict(item)
    flat: dict[str, Any] = getattr(item, "flat", None)
    return flat() if callable(flat) else {}


def enrich_catalog(
    source: Source,
    out: Path,
    *,
    limit: int | None = 100,
    delay: float | None = None,
    want_csv: bool = True,
    refresh: bool = False,
    reporter: Reporter | None = None,
) -> dict[str, Any] | None:
    """
    Дозагружает карточки из чекпоинта и перезаписывает выгрузку.

    `limit=0` (или None) снимает ограничение — обход всего каталога.
    `refresh=True` обогащает заново даже уже помеченные записи.
    """
    report = reporter or Reporter()
    if not getattr(source, "enrichable", True):
        report.warn(f"[!] {source.name}: источник не отдаёт больше данных, чем каталог")
        return None

    out = Path(out)
    checkpoint = out.with_suffix(".jsonl")
    rows = list(iter_rows(checkpoint))
    if not rows:
        report.warn(f"[!] нет чекпоинта: {checkpoint}")
        return None

    by_key = {row_key(source, row): row for row in rows}
    pending = [row for row in by_key.values() if refresh or not row.get(DETAILS_MARK)]
    if limit is not None and limit > 0:
        pending = pending[:limit]

    if not pending:
        report.info(f"[i] все {len(by_key)} записей уже обогащены (обновить: --refresh)")
        return finalize(by_key.values(), out, key=_dedupe_key(source), want_csv=want_csv)

    report.info(f"[i] обогащаю {len(pending)} карточек из {checkpoint.name} (всего {len(by_key)})")
    throttle = Throttle(delay if delay is not None else getattr(source, "delay", 0.0) or 0.0)
    done = 0

    try:
        for index, row in enumerate(pending):
            key = row_key(source, row)
            if index:
                throttle.wait()
            try:
                item = source.details(key)
            except SourceError as exc:
                report.warn(f"[!] {source.name} {key}: {exc}")
                throttle.on_rate_limit()
                continue

            throttle.on_success()
            merge_details(row, flat_of(item))
            row[DETAILS_MARK] = datetime.now(UTC).isoformat(timespec="seconds")
            done += 1

            if done % FLUSH_EVERY == 0:
                write_jsonl(by_key.values(), checkpoint)
                report.info(f"  обогащено {done}/{len(pending)}")
    except KeyboardInterrupt:
        report.warn("\n[!] прервано пользователем — собранное сохранено в чекпоинте")
    finally:
        write_jsonl(by_key.values(), checkpoint)

    written = finalize(by_key.values(), out, key=_dedupe_key(source), want_csv=want_csv)
    failed = len(pending) - done
    report.result(
        f"\n{out.name}: {written['rows']} записей, обогащено {done}"
        + (f", пропущено {failed}" if failed else "")
        + f" за {report.elapsed():.1f} с\n"
        f"  JSON:  {written['json']}\n"
        f"  JSONL: {written['jsonl']}"
        + (f"\n  CSV:   {written['csv']}" if want_csv else "")
    )
    return written


def _dedupe_key(source: Source) -> str:
    return getattr(source, "dedupe_key", "id")