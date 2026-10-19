"""Поиск: одновременно по всем источникам.

Запрос уходит в семь сайтов сразу, выдача приходит по мере ответов и
разложена по источникам: медленный сайт не блокирует быстрый, а его
ошибка не мешает остальным.
"""

from __future__ import annotations

import threading
from typing import Any

import flet as ft

from .. import theme, widgets
from ..hub import cover_of, key_of, search_all, title_of
from ..state import AppState, label_of

QUERY_HINT = "Название тайтла: берсерк, one piece, berserk"


class SearchView:
    """Поиск по всем источникам с группировкой результатов."""

    def __init__(self, app: Any, state: AppState) -> None:
        self.app = app
        self.state = state
        self.query = ""
        self.loading = False
        self.results: dict[str, list[Any]] = {}
        self.errors: dict[str, str] = {}
        self.order: list[str] = []
        self._grids: dict[str, ft.GridView] = {}

        self.field = ft.TextField(
            expand=True,
            hint_text=QUERY_HINT,
            prefix_icon=ft.Icons.SEARCH,
            filled=True,
            fill_color=theme.SURFACE_HIGH,
            border=ft.InputBorder.NONE,
            border_radius=theme.radius(14),
            content_padding=theme.pad(left=14, right=14, top=14, bottom=14),
            color=theme.TEXT,
            hint_style=ft.TextStyle(size=14, color=theme.MUTED),
            text_size=15,
            on_submit=self._submit,
        )
        self.status = ft.Text("", size=12.5, color=theme.MUTED)
        self.body = ft.Column(
            [widgets.state_view(ft.Icons.SEARCH, "Найдите что-нибудь", QUERY_HINT)],
            expand=True,
            spacing=0,
        )
        self.appbar = ft.AppBar(
            title=ft.Text("Поиск"),
            bgcolor=theme.BACKGROUND,
            toolbar_height=68,
        )

    # ---------- поиск ----------

    def _submit(self, e: ft.ControlEvent) -> None:
        query = (self.field.value or "").strip()
        if len(query) < 2:
            widgets.toast(self.app.page, "Введите хотя бы два символа")
            return
        self.search(query)

    def search(self, query: str) -> None:
        self.query = query
        self.results.clear()
        self.errors.clear()
        self.order.clear()
        self._grids.clear()
        self.loading = True
        self.status.value = "Ищем…"
        self._render()

        page = self.app.page
        lock = threading.Lock()

        def on_result(source: str, items: list[Any]) -> None:
            with lock:
                self.results[source] = items
                self.order.append(source)
            self._render()
            page.update()

        def on_error(source: str, message: str) -> None:
            with lock:
                self.errors[source] = message
            # Ошибка источника — это тоже строка в выдаче: без перерисовки
            # она появилась бы только вместе с результатом другого поиска.
            self._render()
            page.update()

        def on_done() -> None:
            self.loading = False
            self._render()
            page.update()

        search_all(
            self.state.hub,
            query,
            on_result=on_result,
            on_error=on_error,
            on_done=on_done,
        )

    # ---------- отрисовка ----------

    def _render(self) -> None:
        if not self.query:
            self.body.controls = [
                widgets.state_view(ft.Icons.SEARCH, "Найдите что-нибудь", QUERY_HINT)
            ]
            self.status.value = ""
            return

        if self.loading and not self.results:
            self.body.controls = [ft.Container(expand=True, content=widgets.spinner("Ищем…"))]
            self.status.value = f"«{self.query}» — ищем по всем источникам"
            return

        if not self.results:
            self.body.controls = [
                widgets.state_view(
                    ft.Icons.SEARCH_OFF,
                    "Ничего не нашли",
                    "Попробуйте другое название или другой источник",
                )
            ]
            self.status.value = f"«{self.query}» — пусто"
            return

        total = sum(len(items) for items in self.results.values())
        self.status.value = f"«{self.query}» — {total} результатов"

        blocks: list[ft.Control] = []
        for source in self.order:
            items = self.results[source]
            if not items:
                continue
            blocks.append(self._block(source, items))
        for source, message in self.errors.items():
            blocks.append(
                ft.Container(
                    padding=theme.pad(left=16, right=16, top=4, bottom=4),
                    content=ft.Text(
                        f"{label_of(source)}: {_short(message)}",
                        size=11.5,
                        color=theme.MUTED,
                    ),
                )
            )
        self.body.controls = blocks

    def _card(self, source: str, item: Any) -> ft.Control:
        return widgets.cover_card(
            item,
            source=source,
            width=150,
            on_click=self._open,
            on_favorite=self._toggle_favorite,
            is_favorite=self.state.library.is_favorite(source, key_of(item)),
            show_source=False,
            state=self.state,
            page=self.app.page,
        )

    def _block(self, source: str, items: list[Any]) -> ft.Container:
        grid = ft.GridView(
            max_extent=150,
            spacing=10,
            run_spacing=10,
            padding=theme.pad(left=16, right=16, top=6, bottom=6),
            child_aspect_ratio=widgets.COVER_RATIO,
            controls=[self._card(source, item) for item in items],
        )
        self._grids[source] = grid
        return ft.Column(
            [
                ft.Container(
                    padding=theme.pad(left=16, right=16, top=16, bottom=2),
                    content=widgets.section_header(
                        label_of(source),
                        ft.Text(f"{len(items)}", size=12.5, color=theme.MUTED),
                    ),
                ),
                grid,
            ],
            spacing=0,
            tight=True,
        )

    def _toggle_favorite(self, item: Any) -> None:
        source = self._source_of(item)
        key = key_of(item)
        library = self.state.library
        if library.is_favorite(source, key):
            library.remove_favorite(source, key)
        else:
            from ..hub import rating_of

            library.add_favorite(source, key, title_of(item), cover_of(item), rating_of(item))
        # Перерисовать только блок этого источника: пересборка всей выдачи
        # переотправляла бы клиенту все найденные карточки заново.
        grid = self._grids.get(source)
        items = self.results.get(source) or []
        if grid is not None and len(grid.controls) == len(items) and items:
            index = next(
                (i for i, it in enumerate(items) if key_of(it) == key), None
            )
            if index is not None:
                grid.controls[index] = self._card(source, items[index])
                self.app.page.update(grid)
                return
        self._render()
        self.app.refresh()

    def _source_of(self, item: Any) -> str:
        raw = getattr(item, "source", "") if not isinstance(item, dict) else item.get("source", "")
        return str(raw or "remanga")

    def _open(self, item: Any) -> None:
        source = self._source_of(item)
        self.app.open_details(source, key_of(item), title_of(item), cover_of(item))

    def body_view(self) -> ft.Column:
        return ft.Column(
            [
                ft.Container(
                    padding=theme.pad(left=16, right=16, top=4, bottom=4),
                    content=self.field,
                ),
                ft.Container(padding=theme.pad(left=16, right=8), content=self.status),
                self.body,
            ],
            expand=True,
            spacing=0,
        )

    def view(self) -> ft.View:
        return ft.View(
            route="/search",
            appbar=self.appbar,
            controls=[
                ft.Container(
                    padding=theme.pad(left=16, right=16, top=4, bottom=4),
                    content=self.field,
                ),
                ft.Container(padding=theme.pad(left=16, right=8), content=self.status),
                self.body,
            ],
            padding=0,
            bgcolor=theme.BACKGROUND,
        )


def _short(text: str, limit: int = 70) -> str:
    text = str(text).replace("\n", " ")
    return text if len(text) <= limit else f"{text[:limit]}…"