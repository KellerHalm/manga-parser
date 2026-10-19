"""Библиотека: избранное, история чтения и скачанные главы.

Всё это лежит в локальной БД и работает без сети, поэтому экран открывается
мгновенно и остаётся полезным в самолёте.
"""

from __future__ import annotations

from typing import Any

import flet as ft

from .. import theme, widgets
from ..state import AppState, label_of
from ..store import Library

TABS = ("Избранное", "История", "Скачанное")


class LibraryView:
    """Три вкладки локального хранилища."""

    def __init__(self, app: Any, state: AppState) -> None:
        self.app = app
        self.state = state
        self.index = 0
        self._tabs_control: ft.Tabs | None = None
        self.body = ft.Column(expand=True, spacing=0)
        self.appbar = ft.AppBar(
            title=ft.Text("Библиотека"),
            bgcolor=theme.BACKGROUND,
        )

    def select(self, index: int) -> None:
        self.index = index
        self.load()

    def load(self) -> None:
        store = self.state.library
        if self.index == 0:
            rows = store.favorites()
            blocks = self._cards(rows, empty=("В избранном пусто", "Нажмите сердечко на обложке"))
            counts = self._counters(store)
            self.body.controls = ([counts] if counts else []) + blocks
        elif self.index == 1:
            self.body.controls = self._history(store)
        else:
            self.body.controls = self._downloads(store)

    def body_view(self) -> ft.Column:
        """
        Содержимое экрана целиком: переключатель вкладок + их содержимое.

        Каркас приложения берёт именно `body_view()`, поэтому вкладки
        обязаны быть здесь: если оставить их только в `view()`, на экране
        их просто не будет.
        """
        return ft.Column([self._tabs(), self.body], expand=True, spacing=0)

    def _tabs(self) -> ft.Tabs:
        """
        Переключатель вкладок.

        У `ft.Tabs` обязателен `length`, а у `ft.TabBar` нет `bgcolor`:
        цвет полосы берётся из темы, а её акцент задаёт `indicator_color`.
        Контрол один на весь экран: пересоздание на каждом refresh
        отправляло бы клиенту вкладки заново целиком.
        """
        if self._tabs_control is None:
            self._tabs_control = ft.Tabs(
                length=len(TABS),
                on_change=lambda e: self.select(e.control.selected_index),
                content=ft.TabBar(
                    indicator_color=theme.PRIMARY,
                    label_color=theme.TEXT,
                    unselected_label_color=theme.MUTED,
                    tabs=[ft.Tab(label=title) for title in TABS],
                ),
            )
        self._tabs_control.selected_index = self.index
        return self._tabs_control

    def view(self) -> ft.View:
        return ft.View(
            route="/library",
            appbar=self.appbar,
            controls=[self.body_view()],
            padding=0,
            bgcolor=theme.BACKGROUND,
        )

    # ---------- вкладки ----------

    def _counters(self, store: Library) -> ft.Control:
        favorites = store.favorites()
        if not favorites:
            return ft.Container()
        by_source: dict[str, int] = {}
        for row in favorites:
            by_source[row["source"]] = by_source.get(row["source"], 0) + 1
        return ft.Container(
            padding=theme.pad(left=16, right=16, top=12, bottom=4),
            content=ft.Row(
                [
                    ft.Text(
                        f"{len(favorites)} тайтлов · "
                        + " · ".join(f"{label_of(s)} {n}" for s, n in by_source.items()),
                        size=12,
                        color=theme.MUTED,
                    )
                ]
            ),
        )

    def _cards(self, rows: list[dict[str, Any]], empty: tuple[str, str]) -> list[ft.Control]:
        if not rows:
            return [
                widgets.state_view(ft.Icons.BOOKMARK_BORDER, empty[0], empty[1])
            ]
        grid = ft.GridView(
            expand=True,
            max_extent=150,
            spacing=12,
            run_spacing=12,
            padding=theme.pad(left=16, right=16, top=8, bottom=96),
            child_aspect_ratio=widgets.COVER_RATIO,
            controls=[
                widgets.cover_card(
                    row,
                    source=row["source"],
                    width=150,
                    on_click=lambda item, r=row: self._open_row(r),
                    on_favorite=lambda item, r=row: self._drop(r),
                    is_favorite=True,
                    subtitle=label_of(row["source"]),
                    state=self.state,
                    page=self.app.page,
                )
                for row in rows
            ],
        )
        return [grid]

    def _history(self, store: Library) -> list[ft.Control]:
        rows = store.history(limit=80)
        if not rows:
            return [
                widgets.state_view(
                    ft.Icons.HISTORY,
                    "История пуста",
                    "Откройте любую главу — здесь появится место, где вы остановились",
                )
            ]
        tiles: list[ft.Control] = []
        for row in rows:
            progress = row.get("page") or 0
            total = row.get("pages") or 0
            bar = (
                ft.ProgressBar(
                    value=(progress / total) if total else 0,
                    color=theme.PRIMARY,
                    bgcolor=theme.SURFACE_TOP,
                    height=3,
                )
                if total
                else ft.Container()
            )
            tiles.append(
                ft.Container(
                    padding=theme.pad(left=16, right=12, top=8, bottom=8),
                    on_click=lambda e, r=row: self._open_row(r),
                    ink=True,
                    content=ft.Row(
                        [
                            # Обложка — через кэш: из него путь подставляется
                            # сразу, а сети здесь нет. Прямой `state.cover()`
                            # качал бы картинку в UI-потоке: у пустого кэша
                            # вкладка «История» замораживала бы окно на
                            # секунды (один сетевой запрос на строку).
                            (
                                box := ft.Container(
                                    width=42,
                                    border_radius=theme.radius(8),
                                    bgcolor=theme.SURFACE_HIGH,
                                    alignment=ft.Alignment.CENTER,
                                    content=ft.Icon(
                                        ft.Icons.IMAGE_OUTLINED,
                                        size=18,
                                        color=theme.MUTED,
                                    ),
                                )
                            ),
                            ft.Column(
                                [
                                    ft.Text(
                                        row["title"],
                                        size=14,
                                        weight=ft.FontWeight.W_500,
                                        color=theme.TEXT,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    ft.Text(
                                        _resume_text(row),
                                        size=11.5,
                                        color=theme.MUTED,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    bar,
                                ],
                                spacing=4,
                                expand=True,
                                tight=True,
                            ),
                            ft.Icon(
                                ft.Icons.CHEVRON_RIGHT, size=18, color=theme.BORDER
                            ),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
            )
            if row.get("cover"):
                widgets.lazy_cover(
                    self.app.page, self.state, row["cover"], box, 42, radius=8
                )
        return [
            ft.Container(
                padding=theme.pad(left=16, right=16, top=10, bottom=6),
                content=ft.Text(
                    "Продолжить чтение", size=15, weight=ft.FontWeight.W_600, color=theme.TEXT
                ),
            ),
            *tiles,
        ]

    def _downloads(self, store: Library) -> list[ft.Control]:
        rows = store.downloads()
        if not rows:
            return [
                widgets.state_view(
                    ft.Icons.DOWNLOAD_FOR_OFFLINE_OUTLINED,
                    "Ничего не скачано",
                    "Скачайте главу с экрана тайтла — она будет доступна без сети",
                )
            ]
        tiles: list[ft.Control] = []
        for row in rows:
            tiles.append(
                ft.Container(
                    padding=theme.pad(left=16, right=12, top=8, bottom=8),
                    on_click=lambda e, r=row: self.app.open_details(
                        r["source"], r["key"], r["title"], None
                    ),
                    ink=True,
                    content=ft.Row(
                        [
                            ft.Container(
                                width=42,
                                height=56,
                                border_radius=theme.radius(8),
                                bgcolor=theme.SURFACE_HIGH,
                                alignment=ft.Alignment.CENTER,
                                content=ft.Icon(
                                    ft.Icons.IMAGE_OUTLINED, size=18, color=theme.MUTED
                                ),
                            ),
                            ft.Column(
                                [
                                    ft.Text(
                                        row["title"],
                                        size=14,
                                        weight=ft.FontWeight.W_500,
                                        color=theme.TEXT,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    ft.Text(
                                        f"{label_of(row['source'])} · "
                                        f"{row.get('label') or 'глава'} · "
                                        f"{row.get('pages') or 0} стр.",
                                        size=11.5,
                                        color=theme.MUTED,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                ],
                                spacing=4,
                                expand=True,
                                tight=True,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.DELETE_OUTLINE,
                                icon_color=theme.MUTED,
                                icon_size=19,
                                tooltip="Удалить",
                                on_click=lambda e, r=row: self._confirm_delete(r),
                            ),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                )
            )
        return [
            ft.Container(
                padding=theme.pad(left=16, right=16, top=10, bottom=6),
                content=ft.Text(
                    f"{len(rows)} глав скачано",
                    size=15,
                    weight=ft.FontWeight.W_600,
                    color=theme.TEXT,
                ),
            ),
            *tiles,
        ]

    # ---------- действия ----------

    def _open_row(self, row: dict[str, Any]) -> None:
        self.app.open_details(row["source"], row["key"], row["title"], row.get("cover"))

    def _drop(self, row: dict[str, Any]) -> None:
        self.state.library.remove_favorite(row["source"], row["key"])
        widgets.toast(self.app.page, "Убрано из избранного", ft.Icons.BOOKMARK_REMOVE)
        self.load()
        self.app.refresh()

    def _confirm_delete(self, row: dict[str, Any]) -> None:
        def remove() -> None:
            chapter = {"id": row["chapter_key"].split(":", 1)[-1]}
            self.state.images.remove_chapter(row["source"], row["key"], chapter)
            self.state.library.remove_download(row["source"], row["key"], chapter)
            widgets.toast(self.app.page, "Глава удалена", ft.Icons.DELETE_OUTLINE)
            self.load()
            self.app.refresh()

        self.app.page.show_dialog(
            widgets.confirm_dialog(
                "Удалить главу?",
                f"«{row['title']}», {row.get('label') or 'глава'}. Файлы будут стёрты с диска.",
                remove,
            )
        )


def _resume_text(row: dict[str, Any]) -> str:
    label = row.get("chapter_label") or "глава"
    page = row.get("page") or 0
    total = row.get("pages") or 0
    where = f"{page + 1}/{total}" if total else str(page + 1)
    return f"{label_of(row['source'])} · {label} · стр. {where}"