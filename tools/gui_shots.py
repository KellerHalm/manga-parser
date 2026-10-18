"""Скриншоты экранов: приложение запускается в реальном окне и снимается."""

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
    print(f"{name:20} {len(path.read_bytes()) // 1024:>5} КБ  route={page.views[-1].route}")


async def shoot(page: ft.Page) -> None:
    app: App = page.data

    app.navigate("/library")
    await asyncio.sleep(1.0)
    page.navigate("/explore")
    await asyncio.sleep(1.5)
    await snap(page, "sources")

    page.navigate("/search")
    await asyncio.sleep(1.0)
    await snap(page, "search-empty")
    app.screens["search"].search("берсерк")
    await asyncio.sleep(30.0)
    await snap(page, "search-results")

    page.navigate("/explore/remanga")
    await asyncio.sleep(20.0)
    await snap(page, "explore")

    app.navigate("/library")
    await asyncio.sleep(4.0)
    await snap(page, "library")

    app.screens["library"].select(1)
    await asyncio.sleep(1.5)
    await snap(page, "history")

    app.navigate("/title/remanga/berserk_of_gluttony_")
    await asyncio.sleep(12.0)
    await snap(page, "details")

    details = app.current
    if getattr(details, "chapters", None):
        details._read(details.chapters[0])
        await asyncio.sleep(16.0)
        await snap(page, "reader-webtoon")
        reader = app.current
        reader._toggle_mode()
        await asyncio.sleep(4.0)
        await snap(page, "reader-paged")

    await page.window.close()


def main(page: ft.Page) -> None:
    page.width = 1000
    page.height = 1500
    page.enable_screenshots = True
    page.window.width = 1000
    page.window.height = 1500
    page.data = App(page)
    page.run_task(shoot, page)


if __name__ == "__main__":
    ft.run(main, view=ft.AppView.FLET_APP_HIDDEN, port=8596)
    print("SHOTS DONE")