"""Обзор: каталог источника с бесконечной пагинацией.

Сетка подгружает следующую страницу, когда до конца осталось меньше двух
экранов: это даёт предзагрузку к скорости прокрутки, но не выкачивает
каталог целиком, если пользователь ушёл с экрана.
"""

from __future__ import annotations

from typing import Any

import flet as ft

from .. import theme, widgets
from ..hub import cover_of, key_of, title_of
from ..state import SOURCE_NOTES, AppState, label_of

#: Сколько экранов не долистать до конца перед подгрузкой.
PREFETCH_SCREENS = 2.0


class ExploreView:
    """Каталог одного источника: сортировка источника + пагинация."""

    def __init__(self, app: Any, state: AppState, source: str) -> None:
        self.app = app
        self.state = state
        self.source = source
        self.page_number = 1
        self.items: list[Any] = []
        self._favorites: set[str] = set()
        self._cards = 0  # сколько карточек уже нарисовано в сетке
        self._note: ft.Control | None = None
        self._loaded = False
        self.loading = False
        self.finished = False
        self.error: str | None = None

        self.grid = ft.GridView(
            expand=True,
            max_extent=170,
            spacing=12,
            run_spacing=12,
            padding=theme.pad(left=16, right=16, top=8, bottom=96),
            child_aspect_ratio=widgets.COVER_RATIO,
            on_scroll=self._on_scroll,
        )
        self.footer = ft.Container(content=widgets.spinner(), visible=False)
        self.appbar = ft.AppBar(
            title=ft.Text(label_of(source)),
            bgcolor=theme.BACKGROUND,
            actions=[
                ft.IconButton(
                    icon=ft.Icons.REFRESH,
                    icon_color=theme.MUTED,
                    tooltip="Обновить",
                    on_click=lambda e: self.reload(),
                )
            ],
        )

    # ---------- загрузка ----------

    def load_first(self, force: bool = False) -> None:
        """
        Загрузить каталог с первой страницы.

        Повторный заход на экран не сбрасывает каталог: данные уже на месте,
        а новая загрузка выковыривала бы пользователю всё скачанное и
        возвращала бы прокрутку в начало. Сброс — только по кнопке
        «Обновить» (`force=True`) или после ошибки загрузки.
        """
        if not force and self._loaded and not self.error:
            self._render_body()
            return
        self.items.clear()
        self._favorites.clear()
        self._cards = 0
        self._note = None
        self.grid.controls = []
        self.page_number = 1
        self.finished = False
        self.error = None
        self.grid.visible = True
        self._render_body()
        self.load_more(initial=True)

    def reload(self) -> None:
        self.load_first(force=True)

    def load_more(self, initial: bool = False) -> None:
        if self.loading or self.finished:
            return
        self.loading = True
        self.error = None
        self._render_body()
        source = self.source
        number = self.page_number

        def worker() -> None:
            try:
                found = list(self.state.hub.client(source).catalog(page=number))
                self.items.extend(found)
                self._favorites.update(key_of(item) for item in found)
                self.finished = not found
                self.page_number = number + 1
                self._loaded = True
            except Exception as exc:
                self.error = str(exc)
            finally:
                self.loading = False
                self._render_body()
                self.app.refresh()

        self.app.page.run_thread(worker)

    def _on_scroll(self, e: ft.OnScrollEvent) -> None:
        if self.loading or self.finished:
            return
        remaining = e.max_scroll_extent - e.pixels
        if remaining < PREFETCH_SCREENS * max(e.viewport_dimension, 1):
            self.load_more()

    # ---------- отрисовка ----------

    def _card(self, item: Any) -> ft.Control:
        return widgets.cover_card(
            item,
            source=self.source,
            width=170,
            on_click=self._open,
            on_favorite=self._toggle_favorite,
            is_favorite=self._is_favorite(item),
            state=self.state,
            page=self.app.page,
        )

    def _render_body(self) -> None:
        if self.error and not self.items:
            self.grid.controls = []
            self._cards = 0
            self.grid.visible = False
            self.footer.visible = False
            return

        self.grid.visible = True
        cards = self.grid.controls
        # Дописать только новые карточки: пересборка всей сетки на каждой
        # странице отправляла бы клиенту все обложки заново (чем дальше
        # прокрутка, тем больше) и перезапускала бы их загрузку с CDN.
        if self._cards > len(self.items):
            self.grid.controls = []
            self._cards = 0
            cards = self.grid.controls
        if cards and cards[-1] is self._note:
            cards.pop()  # заметка всегда в хвосте, но карточек больше неё
        while self._cards < len(self.items):
            cards.append(self._card(self.items[self._cards]))
            self._cards += 1
        note = SOURCE_NOTES.get(self.source)
        if note:
            self._note = self._note_row(note)
            cards.append(self._note)

        if self.loading:
            self.footer.visible = True
            self.footer.content = widgets.spinner()
        elif self.error:
            self.footer.visible = True
            self.footer.content = ft.TextButton(
                content=ft.Text(f"Ошибка загрузки: {_short(self.error)} — повторить"),
                on_click=lambda e: self.load_more(),
            )
        elif self.finished and self.items:
            self.footer.visible = True
            self.footer.content = ft.Text(
                f"Это все {len(self.items)} тайтлов", size=12.5, color=theme.MUTED
            )
        else:
            self.footer.visible = False

    def _note_row(self, note: str) -> ft.Control:
        return ft.Container(
            height=60,
            alignment=ft.Alignment.CENTER_LEFT,
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.INFO_OUTLINE, size=15, color=theme.MUTED),
                    ft.Text(note, size=12, color=theme.MUTED, expand=True),
                ],
                spacing=8,
            ),
        )

    def _is_favorite(self, item: Any) -> bool:
        return key_of(item) in self._favorites

    def _toggle_favorite(self, item: Any) -> None:
        key = key_of(item)
        library = self.state.library
        if key in self._favorites:
            library.remove_favorite(self.source, key)
            self._favorites.discard(key)
            widgets.toast(self.app.page, "Убрано из библиотеки", ft.Icons.BOOKMARK_REMOVE)
        else:
            library.add_favorite(
                self.source, key, title_of(item), cover_of(item), _rating(item)
            )
            self._favorites.add(key)
            widgets.toast(self.app.page, "Добавлено в библиотеку", ft.Icons.BOOKMARK_ADD)
        # Перерисовать только плитку этого тайтла: пересборка сетки
        # скидывала бы прокрутку и переотправляла бы клиенту все обложки.
        cards = self.grid.controls
        limit = min(self._cards, len(self.items), len(cards))
        for index in range(limit):
            if key_of(self.items[index]) == key:
                cards[index] = self._card(self.items[index])
                break
        self.app.page.update(self.grid)

    def _open(self, item: Any) -> None:
        self.app.open_details(self.source, key_of(item), title_of(item), cover_of(item))

    def view(self) -> ft.View:
        body: list[ft.Control] = [self.grid, self.footer]
        if not self.items and self.loading:
            body = [ft.Container(expand=True, content=widgets.spinner("Загрузка каталога…"))]
        elif not self.items and self.error:
            body = [
                widgets.state_view(
                    ft.Icons.CLOUD_OFF,
                    "Каталог недоступен",
                    _short(self.error),
                    ft.FilledButton(
                        content=ft.Text("Повторить"),
                        on_click=lambda e: self.reload(),
                    ),
                )
            ]
        content = ft.Container(content=ft.Stack(body, expand=True), expand=True)
        view = ft.View(
            route=f"/explore/{self.source}",
            appbar=self.appbar,
            controls=[content],
            padding=0,
            bgcolor=theme.BACKGROUND,
            floating_action_button=self._source_switch(),
            floating_action_button_location=ft.FloatingActionButtonLocation.END_FLOAT,
        )
        # Каталог тоже встаёт в раскладку приложения: на десктопе сайдбар,
        # на телефоне нижние вкладки — иначе уйти с экрана можно только
        # кнопкой «назад» в шапке.
        view.data = content
        return view

    def _source_switch(self) -> ft.Control:
        """Плавающая кнопка возврата к списку источников."""
        return ft.FloatingActionButton(
            icon=ft.Icons.SWAP_HORIZ,
            tooltip="Сменить источник",
            bgcolor=theme.SURFACE_TOP,
            foreground_color=theme.TEXT,
            mini=True,
            on_click=lambda e: self.app.navigate("/explore"),
        )


def _rating(item: Any) -> float | None:
    from ..hub import rating_of

    return rating_of(item)


def _short(text: str, limit: int = 90) -> str:
    text = str(text).replace("\n", " ")
    return text if len(text) <= limit else f"{text[:limit]}…"