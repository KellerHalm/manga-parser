"""Живая проверка пути: каталог -> карточка -> главы -> читалка."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flet as ft  # noqa: E402

from mangaparser.gui import hub  # noqa: E402
from mangaparser.gui.app import App  # noqa: E402

SOURCE = "remanga"


async def drive(page: ft.Page) -> None:
    app: App = page.data

    await asyncio.sleep(0.3)
    app.navigate(f"/explore/{SOURCE}")
    await asyncio.sleep(6)

    view = app.explore_views.get(SOURCE)
    items = view.items if view else []
    print(f"каталог: {len(items)} тайтлов, ошибка={getattr(view, 'error', None)}")
    if not items:
        print("НЕТ ДАННЫХ")
        await page.window.close()
        return

    item = items[0]
    key = hub.key_of(item)
    print(f"первый тайтр: {hub.title_of(item)!r} key={key!r} cover={hub.cover_of(item)[:60] if hub.cover_of(item) else None}")

    app.open_details(SOURCE, key, hub.title_of(item), hub.cover_of(item))
    await asyncio.sleep(6)
    view_obj = app.current
    print(f"карточка построена: {type(view_obj).__name__} route={page.views[-1].route}")

    await asyncio.sleep(6)
    if view_obj:
        print(f"детали: глав={len(view_obj.chapters)} ошибка={view_obj.error}")
        print(f"описание: {(hub.description_of(view_obj.item) or '')[:90]!r}")
        print(f"жанры: {hub.genres_of(view_obj.item)[:8]}")
        if view_obj.chapters:
            chapter = view_obj.chapters[0]
            app.open_reader(SOURCE, key, view_obj.title, view_obj.cover, chapter, view_obj.chapters)
            await asyncio.sleep(8)
            reader = app.current
            if reader:
                print(f"читалка: страниц={len(reader.urls)} ошибка={reader.error} режим={reader.mode}")
                if reader.urls:
                    await asyncio.sleep(6)
                    ready = sum(1 for f in reader.files if f)
                    print(f"загружено страниц: {ready} из {len(reader.urls)}")
                    reader._toggle_mode()
                    await asyncio.sleep(2)
                    print(f"после переключения: режим={reader.mode}, пейджер={type(reader._pager).__name__ if reader._pager else None}")
                    reader._step(1)
                    print(f"переход на стр. {reader.current + 1}")
    await page.window.close()


def main(page: ft.Page) -> None:
    page.width = 900
    page.height = 900
    page.data = App(page)
    page.run_task(drive, page)


if __name__ == "__main__":
    ft.run(main, view=ft.AppView.FLET_APP_HIDDEN, port=8580)
    print("E2E DONE")