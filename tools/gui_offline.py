"""Проверка скачивания главы и офлайн-чтения без сети."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flet as ft  # noqa: E402

from mangaparser.gui.app import App  # noqa: E402

SOURCE = "remanga"
KEY = "barakamon"


async def drive(page: ft.Page) -> None:
    app: App = page.data
    library = app.state.library

    app.open_details(SOURCE, KEY, "Баракамон", None)
    await asyncio.sleep(8)
    details = app.current
    chapters = details.chapters
    print(f"глав в карточке: {len(chapters)}")
    if not chapters:
        app.close()
        await page.window.close()
        return

    chapter = chapters[-1]
    library.add_favorite(SOURCE, KEY, "Баракамон", "cover.jpg", 8.1, "Выпускается")
    print(f"избранное: {[r['title'] for r in library.favorites()]}")

    details._download(chapter)
    for _ in range(40):
        await asyncio.sleep(1)
        if library.is_downloaded(SOURCE, KEY, chapter):
            break
    rows = library.downloads()
    print(f"скачано глав: {len(rows)}, страниц: {rows[0]['pages'] if rows else 0}")

    files = app.state.images.local_chapter(SOURCE, KEY, chapter)
    print(f"файлов на диске: {len(files)}")
    print(f"размер главы: {sum(Path(f).stat().st_size for f in files) // 1024} КБ")

    reader = app.current
    reader.save if hasattr(reader, "save") else None
    details._read(chapter)
    await asyncio.sleep(4)
    offline = app.current
    ready = sum(1 for f in offline.files if f)
    print(f"офлайн-читалка: страниц={len(offline.urls)} готово={ready} offline={offline.offline} режим={offline.mode}")
    print(f"источник картинок: {str(offline.files[0])[:70] if offline.files[0] else None}")
    offline._step(1)
    offline._save(force=True)
    await asyncio.sleep(0.5)
    print(f"прогресс сохранён: {library.progress(SOURCE, KEY)}")

    app.navigate("/library")
    await asyncio.sleep(1)
    print(f"библиотека: избранное={len(library.favorites())} история={len(library.history())} скачанное={len(library.downloads())}")

    app.close()
    await page.window.close()


def main(page: ft.Page) -> None:
    page.width = 900
    page.height = 900
    page.data = App(page)
    page.run_task(drive, page)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        import mangaparser.gui.paths as paths
        import mangaparser.gui.store as store

        store.db_path = lambda: Path(tmp) / "library.sqlite3"  # type: ignore[assignment]
        del paths
        try:
            ft.run(main, view=ft.AppView.FLET_APP_HIDDEN, port=8590)
        finally:
            app_dir = Path(tmp)
            for leftover in app_dir.glob("library.sqlite3*"):
                leftover.unlink(missing_ok=True)
    print("OFFLINE DONE")