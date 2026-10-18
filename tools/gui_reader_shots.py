"""Читалка: оба режима снимаются явно, без зависимости от настроек."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flet as ft  # noqa: E402

from mangaparser.gui.app import App  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "shots"
OUT.mkdir(exist_ok=True)


async def snap(page: ft.Page, name: str) -> None:
    path = OUT / f"{name}.png"
    path.write_bytes(await page.take_screenshot())
    print(f"{name:18} {len(path.read_bytes()) // 1024:>5} КБ")


async def drive(page: ft.Page) -> None:
    app: App = page.data
    app.state.reader_mode = "webtoon"
    app.navigate("/library")
    await asyncio.sleep(1.0)

    app.navigate("/title/remanga/berserk_of_gluttony_")
    await asyncio.sleep(1.0)
    await asyncio.sleep(12)
    details = app.current
    details._read(details.chapters[1])
    await asyncio.sleep(18)

    reader = app.current
    print("mode:", reader.mode, "ready:", sum(1 for f in reader.files if f), "/", len(reader.urls),
          "content:", type(reader.container.content).__name__,
          "list:", type(reader._list).__name__ if reader._list else None,
          "boxes:", len(reader._controls),
          "box0:", type(reader._controls[0].content).__name__ if reader._controls else None)
    await snap(page, "reader-webtoon")
    print("src0:", str(reader._controls[0].content.content.src)[-40:])
    reader._list.controls = list(reader._controls)
    page.update()
    await asyncio.sleep(3.0)
    await snap(page, "reader-webtoon-2")

    reader._toggle_mode()
    await asyncio.sleep(3)
    print("mode:", reader.mode, "content:", type(reader.container.content).__name__,
          "list:", type(reader._list).__name__ if reader._list else None,
          "pager:", type(reader._pager).__name__ if reader._pager else None)
    await snap(page, "reader-paged")

    reader.controls_visible = False
    reader._render()
    app.refresh()
    await asyncio.sleep(1.5)
    await snap(page, "reader-clean")

    await page.window.close()


def main(page: ft.Page) -> None:
    page.width = 800
    page.height = 1200
    page.enable_screenshots = True
    page.window.width = 800
    page.window.height = 1200
    page.data = App(page)
    page.run_task(drive, page)


if __name__ == "__main__":
    ft.run(main, view=ft.AppView.FLET_APP_HIDDEN, port=8612)
    print("READER SHOTS DONE")