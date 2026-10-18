"""End-to-end проверка кнопок читалки на настоящем клиенте Flet.

Интерфейсные тесты (gui_interactions) вызывают обработчики напрямую и видят
только состояние Python. Здесь то же самое проверяется вживую: приложение
открывается скрытым окном, кнопки жмутся через настоящую обработку событий,
а результат фиксируется скриншотами — если содержимое экрана после «нажатия»
не меняется, кнопка не работает так, как её видит пользователь.

Сеть нужна настоящая, источник — remanga; скриншоты падают в shots/probe/.
"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flet as ft  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from mangaparser.gui.app import App  # noqa: E402

SOURCE = "remanga"
KEY = "barakamon"
OUT = Path(__file__).resolve().parents[1] / "shots" / "probe"
OUT.mkdir(parents=True, exist_ok=True)


def diff(before: bytes, after: bytes) -> float:
    """Сколько пикселей изменилось после нажатия кнопки, в процентах."""
    a = np.asarray(Image.open(io.BytesIO(before)).convert("L"), dtype=np.int16)
    b = np.asarray(Image.open(io.BytesIO(after)).convert("L"), dtype=np.int16)
    if a.shape != b.shape:
        return 100.0
    return float((np.abs(a - b) > 8).mean() * 100)


async def snap(page: ft.Page, name: str) -> bytes:
    data = await page.take_screenshot()
    (OUT / f"{name}.png").write_bytes(data)
    return data


async def drive(page: ft.Page) -> None:
    app: App = page.data
    app.state.reader_mode = "paged"
    try:
        app.navigate(f"/title/{SOURCE}/{KEY}")
        for _ in range(45):
            await asyncio.sleep(1)
            if type(app.current).__name__ == "DetailsView":
                break
        else:
            app.navigate(f"/title/{SOURCE}/{KEY}")
            for _ in range(30):
                await asyncio.sleep(1)
                if type(app.current).__name__ == "DetailsView":
                    break
        if type(app.current).__name__ != "DetailsView":
            print("FAIL: карточка не загрузилась:", type(app.current).__name__)
            return
        await asyncio.sleep(12)
        details = app.current
        details._read(details.chapters[1])
        for _ in range(20):
            await asyncio.sleep(1)
            if type(app.current).__name__ == "ReaderView" and len(app.current.urls):
                break
        reader = app.current
        if type(reader).__name__ != "ReaderView":
            print("FAIL: читалка не открылась, открыто", type(reader).__name__)
            return
        print("ready:", sum(1 for f in reader.files if f), "/", len(reader.urls))

        next_btn = reader.bottom_bar.content.controls[1].controls[-1]
        prev_btn = reader.bottom_bar.content.controls[1].controls[0]

        # --- кнопки листания в постраничном режиме ---
        b0 = await snap(page, "paged-0")
        await next_btn._trigger_event("click", {})
        await asyncio.sleep(1.5)
        b1 = await snap(page, "paged-1")
        print(f"  next(paged): changed={diff(b0, b1):.1f}% page={reader.current + 1}")
        await prev_btn._trigger_event("click", {})
        await asyncio.sleep(1.5)
        b2 = await snap(page, "paged-2")
        print(f"  prev(paged): changed={diff(b1, b2):.1f}% page={reader.current + 1}")

        # --- кнопки листания в вебтунсе: список должен проскроллиться ---
        reader._toggle_mode()
        await asyncio.sleep(2)
        w0 = await snap(page, "webtoon-0")
        await next_btn._trigger_event("click", {})
        await asyncio.sleep(1.5)
        w1 = await snap(page, "webtoon-1")
        print(f"  next(webtoon): changed={diff(w0, w1):.1f}% page={reader.current + 1}")
        await prev_btn._trigger_event("click", {})
        await asyncio.sleep(1.5)
        w1b = await snap(page, "webtoon-prev")
        print(f"  prev(webtoon): changed={diff(w1, w1b):.1f}% page={reader.current + 1}")

        # --- тап по центру прячет панели, тап по краю листает ---
        reader.controls_visible = False
        reader._render()
        app.refresh()
        await asyncio.sleep(1)
        w2 = await snap(page, "webtoon-clean")
        print(f"  hide-bars: changed={diff(w1b, w2):.1f}%")
        reader.controls_visible = True
        reader._render()
        app.refresh()
        await asyncio.sleep(1)
        w3 = await snap(page, "webtoon-bars")
        print(f"  show-bars: changed={diff(w2, w3):.1f}%")

        # --- назад к тайтлу ---
        back_btn = reader.top_bar.content.controls[0]
        await back_btn._trigger_event("click", {})
        await asyncio.sleep(3)
        print("  back ->", type(app.current).__name__, app.page.route)

    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        raise
    finally:
        await page.window.close()


def main(page: ft.Page) -> None:
    page.enable_screenshots = True
    page.data = App(page)
    page.run_task(drive, page)


if __name__ == "__main__":
    ft.run(main, view=ft.AppView.FLET_APP_HIDDEN, port=8619)
    print("PROBE DONE")
