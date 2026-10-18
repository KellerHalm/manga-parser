"""Каркас приложения: адаптивная навигация и маршруты.

Один `App` на сессию Flet. На телефоне вкладки живут внизу (`NavigationBar`),
на широком окне — в сайдбаре (`NavigationRail`); выбор происходит по ширине
окна, поэтому приложение одинаково выглядит на Android, iOS и десктопе.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, unquote

import flet as ft

from . import theme
from .state import AppState
from .views.details import DetailsView
from .views.explore import ExploreView
from .views.library import LibraryView
from .views.reader import ReaderView
from .views.search import SearchView
from .views.sources import SourcesView

ROOT = "/library"
RAIL_BREAKPOINT = 720  # уже — телефон, шире — планшет/десктоп

TABS = (
    ("/library", "Библиотека", ft.Icons.LIBRARY_BOOKS_OUTLINED, ft.Icons.LIBRARY_BOOKS),
    ("/explore", "Обзор", ft.Icons.EXPLORE_OUTLINED, ft.Icons.EXPLORE),
    ("/search", "Поиск", ft.Icons.SEARCH_OUTLINED, ft.Icons.SEARCH),
)


class App:
    """Навигация, экраны и общее состояние окна."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.state = AppState(page)
        self.explore_views: dict[str, ExploreView] = {}
        self.screens: dict[str, Any] = {}
        self.current: Any = None
        self.history: list[str] = [ROOT]
        self._going_back = False
        self._pending_title: tuple[str, str, str, str | None] | None = None
        self._pending_reader: ReaderView | None = None

        page.title = "Manga"
        page.padding = 0
        page.spacing = 0
        theme.apply(page, accent_color=self.state.accent)
        page.on_route_change = self._on_route_change
        page.on_view_pop = self._on_view_pop
        page.on_resize = self._on_resize

    # ---------- маршруты ----------

    def navigate(self, route: str) -> None:
        self.page.navigate(route)

    def back(self) -> None:
        """
        Вернуться на предыдущий экран.

        Историю ведём сами: `page.views` здесь всегда содержит ровно один
        экран (на каждый маршрут он строится заново), поэтому «предыдущий
        маршрут» из стега Flet взять нельзя — без своей истории кнопка «назад»
        из читалки уводила бы в библиотеку вместо карточки тайтла.
        """
        if len(self.history) > 1:
            self.history.pop()
            self._going_back = True
            self.page.navigate(self.history[-1])
        else:
            self.page.navigate(ROOT)

    def _on_view_pop(self, e: ft.ViewPopEvent) -> None:
        """Системная кнопка «назад» и стрелка в шапке."""
        if not self._going_back:
            self.back()

    def _remember(self, route: str) -> None:
        if self._going_back:
            # маршрут уже в истории — это переход назад, не новая запись
            self._going_back = False
            return
        if self.history and self.history[-1] == route:
            return
        self.history.append(route)

    def _on_resize(self, e: Any) -> None:
        self._apply_shell()
        self.page.update()

    def _on_route_change(self, e: ft.RouteChangeEvent) -> None:
        route = e.route or ROOT
        parts = self._split(route)
        self._remember(route)
        self.page.views.clear()
        self.page.views.append(self._build(route, parts))
        # Навигация вешается на уже прикреплённый верхний экран: иначе
        # `_apply_shell` смотрит на пустой стек и не находит экрана.
        self._apply_shell()
        self.page.update()

    def _split(self, route: str) -> list[str]:
        return [unquote(part) for part in (route or ROOT).strip("/").split("/") if part]

    def _build(self, route: str, parts: list[str]) -> ft.View:
        head = parts[0] if parts else ""

        if head == "explore" and len(parts) > 1:
            screen = self._explore(parts[1])
        elif head == "title" and len(parts) > 2:
            screen = self._details(parts[1], parts[2])
        elif head == "read":
            screen = self._reader()
        else:
            return self._shell(self._tab_route(route))

        self.current = screen
        return screen.view()

    def _tab_route(self, route: str) -> str:
        for path, *_rest in TABS:
            if route.startswith(path):
                return path
        return ROOT

    # ---------- экраны с вкладками ----------

    def _explore(self, source: str) -> ExploreView:
        view = self.explore_views.get(source)
        if view is None:
            view = ExploreView(self, self.state, source)
            self.explore_views[source] = view
        view.load_first()
        return view

    def _details(self, source: str, key: str) -> DetailsView:
        title = "Загрузка…"
        cover: str | None = None
        pending = self._pending_title
        if pending and pending[0] == source and pending[1] == key:
            title, cover = pending[2], pending[3]
        self._pending_title = None
        view = DetailsView(self, self.state, source, key, title, cover)
        view.load()
        return view

    def _reader(self) -> ReaderView:
        reader = self._pending_reader
        if reader is None:

            placeholder = ReaderView(self, self.state, "", "", "Нет главы", None, {}, [])
            placeholder.load = lambda: None  # type: ignore[method-assign]
            placeholder.error = "Глава не выбрана: вернитесь к тайтлу"
            placeholder.loading = False
            return placeholder
        self._pending_reader = None
        return reader

    def _shell(self, route: str) -> ft.View:
        screen = self._screen(route)
        self.current = screen
        content = ft.Container(content=screen.body_view(), expand=True)
        view = ft.View(
            route=route,
            appbar=screen.appbar,
            controls=[content],
            padding=0,
            bgcolor=theme.BACKGROUND,
        )
        view.data = content
        return view

    def _screen(self, route: str) -> Any:
        if route == "/search":
            screen = self._cached("search", lambda: SearchView(self, self.state))
        elif route == "/explore":
            screen = self._cached("sources", lambda: SourcesView(self, self.state))
        else:
            screen = self._cached("library", lambda: LibraryView(self, self.state))
        return screen

    def _cached(self, name: str, factory: Any) -> Any:
        screen = self.screens.get(name)
        if screen is None:
            screen = factory()
            self.screens[name] = screen
        load = getattr(screen, "load", None)
        if callable(load):
            load()
        return screen

    # ---------- нижняя панель / сайдбар ----------

    def _wide(self) -> bool:
        return (self.page.width or 0) >= RAIL_BREAKPOINT

    def _apply_shell(self) -> None:
        """Перевесить навигацию под текущую ширину окна.

        Идемпотентно: содержимое экрана не пересобирается, только раскладка.
        """
        view = self.page.views[-1] if self.page.views else None
        if view is None:
            return
        content = view.data
        if not isinstance(content, ft.Container):
            return

        if self._wide():
            rail = ft.NavigationRail(
                selected_index=self._active_tab(),
                min_width=76,
                bgcolor=theme.BACKGROUND,
                destinations=self._destinations(ft.NavigationRailDestination),
                on_change=self._on_tab_change,
            )
            view.controls = [ft.Row([rail, content], expand=True, spacing=0)]
            view.navigation_bar = None
        else:
            view.controls = [content]
            view.navigation_bar = ft.NavigationBar(
                selected_index=self._active_tab(),
                bgcolor=theme.SURFACE_TOP,
                destinations=self._destinations(ft.NavigationBarDestination),
                on_change=self._on_tab_change,
            )

    def _destinations(self, kind: Any) -> list[Any]:
        return [
            kind(icon=ft.Icon(icon), selected_icon=ft.Icon(active), label=label)
            for _route, label, icon, active in TABS
        ]

    def _active_tab(self) -> int:
        route = self.page.route or ROOT
        for index, (path, *_rest) in enumerate(TABS):
            if route.startswith(path):
                return index
        return 0

    def _on_tab_change(self, e: ft.ControlEvent) -> None:
        index = int(getattr(e.control, "selected_index", 0) or 0)
        if not 0 <= index < len(TABS):
            return
        # Переход между разделами — новая точка отсчёта: «назад» из каталога
        # ведёт в библиотеку, а не в карточку тайтла, из которой попали.
        self.history.clear()
        self.page.navigate(TABS[index][0])

    def refresh(self) -> None:
        """
        Перерисовать окно после изменения данных.

        Экран пересобирается целиком: `view()` возвращает новый `ft.View`
        с теми же контролами, поэтому состояние (сетка, прочитанное, позиция)
        сохраняется, а свежие данные попадают на экран. Экраны, которые
        перерисовывают себя сами (читалка), помечаются `manages_view`.
        """
        screen = self.current
        if screen is not None and self.page.views and not getattr(
            screen, "manages_view", False
        ):
            self.page.views[-1] = screen.view()
        self._apply_shell()
        self.page.update()

    # ---------- переходы между экранами ----------

    def open_details(self, source: str, key: str, title: str, cover: str | None) -> None:
        self._pending_title = (source, key, title, cover)
        self.page.navigate(f"/title/{quote(source)}/{quote(key)}")

    def open_reader(
        self,
        source: str,
        key: str,
        title: str,
        cover: str | None,
        chapter: Any,
        chapters: list[Any],
        replace: bool = False,
    ) -> None:
        reader = ReaderView(self, self.state, source, key, title, cover, chapter, chapters)
        if replace and self.page.views:
            # Переход на соседнюю главу: меняем верхний экран, чтобы «назад»
            # вернул к тайтлу, а не к предыдущей главе.
            self._pending_reader = None
            self.page.views[-1] = reader.view()
            reader.load()
            self.page.update()
            return
        self._pending_reader = reader
        self.page.navigate(f"/read/{quote(source)}/{quote(key)}")
        reader.load()

    def close(self) -> None:
        self.state.close()


def main(page: ft.Page) -> None:
    """Точка входа Flet."""
    page.data = App(page)
    page.navigate(ROOT)