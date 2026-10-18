"""Проверка экранов без сети и без окна: маршруты строятся, исключений нет."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flet as ft  # noqa: E402

from mangaparser.gui import paths  # noqa: E402
from mangaparser.gui.app import App  # noqa: E402

ROUTES = ["/library", "/explore", "/search", "/explore/remanga", "/title/remanga/x", "/library"]


async def drive(page: ft.Page) -> None:
    for route in ROUTES:
        page.navigate(route)
        await asyncio.sleep(0.35)
        view = page.views[-1]
        print(f"route={route:22} view={view.route:26} controls={len(view.controls)}")


def main(page: ft.Page) -> None:
    page.width = 420
    page.height = 900
    page.data = App(page)
    page.run_task(drive, page)


if __name__ == "__main__":
    paths.db_path().parent.mkdir(parents=True, exist_ok=True)
    ft.run(main, view=ft.AppView.FLET_APP_HIDDEN, port=8570)
    print("SMOKE DONE")