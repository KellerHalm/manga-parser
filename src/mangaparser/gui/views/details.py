"""Экран тайтла: карточка, главы, чтение и скачивание.

Карточка грузится отдельно от каталога: у большинства источников в списке
есть только название и обложка, а описание с жанрами приходит из `details()`.
Поэтому сначала показывается то, что уже есть, потом — полная карточка.
"""

from __future__ import annotations

from typing import Any

import flet as ft

from .. import theme, widgets
from ..hub import (
    author_of,
    cover_of,
    description_of,
    genres_of,
    rating_of,
    status_of,
    title_of,
)
from ..state import SOURCE_NOTES, AppState, label_of
from ..store import chapter_key

SORTS = (
    ("По номеру", "number"),
    ("Новые", "new"),
    ("Старые", "old"),
)


class DetailsView:
    """Карточка тайтла и список глав."""

    def __init__(
        self,
        app: Any,
        state: AppState,
        source: str,
        key: str,
        title: str,
        cover: str | None,
    ) -> None:
        self.app = app
        self.state = state
        self.source = source
        self.key = key
        self.title = title
        self.cover = cover
        self.item: Any = None
        self.chapters: list[Any] = []
        self.chapter_sort = "number"
        self.loading_details = False
        self.loading_chapters = False
        self.error: str | None = None

        self.scroll = ft.Column(expand=True, spacing=0, scroll=ft.ScrollMode.AUTO)
        self.title_bar = ft.AppBar(
            title=ft.Text(title, size=17),
            bgcolor=theme.BACKGROUND,
            leading=ft.IconButton(
                icon=ft.Icons.ARROW_BACK,
                icon_color=theme.TEXT,
                on_click=lambda e: self.app.back(),
            ),
            actions=[self._favorite_button()],
        )

    # ---------- загрузка ----------

    def load(self) -> None:
        self.scroll.controls = [
            ft.Container(
                padding=theme.pad(left=16, right=16, top=20, bottom=20),
                content=ft.Column(
                    [
                        ft.ProgressRing(width=26, height=26, stroke_width=2.4),
                        ft.Text("Загружаем карточку…", size=13, color=theme.MUTED),
                    ],
                    spacing=12,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            )
        ]
        self._load_details()
        self._load_chapters()

    def _load_details(self) -> None:
        self.loading_details = True
        client = self.state.hub.client(self.source)

        def worker() -> None:
            try:
                self.item = client.details(self.key)
                if self.item is not None:
                    self.title = title_of(self.item)
                    self.cover = cover_of(self.item) or self.cover
            except Exception as exc:
                self.error = str(exc)
            finally:
                self.loading_details = False
                self._render()
                self.app.refresh()

        self.app.page.run_thread(worker)

    def _load_chapters(self) -> None:
        self.loading_chapters = True
        client = self.state.hub.client(self.source)

        def worker() -> None:
            try:
                chapters = list(client.chapters(self.key))
                # Позиция в выдаче сайта — единственное, чем можно назвать
                # главу, если источник не отдаёт номер (у ReManga в записи
                # главы лежит только внутренний id). Номер при этом не
                # подделывается: сортировка работает по настоящим числам,
                # а подпись — по позиции.
                for position, chapter in enumerate(chapters, 1):
                    if isinstance(chapter, dict) and "_pos" not in chapter:
                        chapter["_pos"] = position
                self.chapters = chapters
            except Exception as exc:
                self.error = str(exc)
            finally:
                self.loading_chapters = False
                self._render()
                self.app.refresh()

        self.app.page.run_thread(worker)

    # ---------- отрисовка ----------

    def _favorite_button(self) -> ft.Control:
        favorite = self.state.library.is_favorite(self.source, self.key)
        return ft.IconButton(
            icon=ft.Icons.FAVORITE if favorite else ft.Icons.FAVORITE_BORDER,
            icon_color=theme.PRIMARY if favorite else theme.MUTED,
            tooltip="В библиотеку",
            on_click=lambda e: self._toggle_favorite(),
        )

    def _toggle_favorite(self) -> None:
        library = self.state.library
        if library.is_favorite(self.source, self.key):
            library.remove_favorite(self.source, self.key)
            widgets.toast(self.app.page, "Убрано из библиотеки", ft.Icons.BOOKMARK_REMOVE)
        else:
            library.add_favorite(
                self.source,
                self.key,
                self.title,
                self.cover,
                rating_of(self.item) if self.item is not None else None,
                status_of(self.item) if self.item is not None else None,
            )
            widgets.toast(self.app.page, "Добавлено в библиотеку", ft.Icons.BOOKMARK_ADD)
        self.title_bar.actions = [self._favorite_button()]
        self.app.refresh()

    def _render(self) -> None:
        blocks: list[ft.Control] = [self._hero(), self._facts(), self._genres(), self._actions()]
        if description_of(self.item):
            blocks.append(self._description())
        blocks.append(self._chapters_block())
        self.scroll.controls = blocks
        self.title_bar.title = ft.Text(self.title, size=17, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)

    def _hero(self) -> ft.Control:
        cover_src = self.state.cover(self.cover, self.source) or self.cover
        width = 116.0 if self.app.page.width < 420 else 132.0
        info: list[ft.Control] = [
            ft.Text(
                self.title,
                size=18 if self.app.page.width < 420 else 20,
                weight=ft.FontWeight.W_700,
                color=theme.TEXT,
                height=1.2,
            ),
            ft.Row(
                [
                    widgets.source_dot(self.source),
                    ft.Text(
                        label_of(self.source),
                        size=12.5,
                        color=widgets.source_tint(self.source),
                        weight=ft.FontWeight.W_600,
                    ),
                ],
                spacing=6,
            ),
        ]
        rating = rating_of(self.item) if self.item is not None else None
        if rating is not None:
            info.append(
                ft.Row(
                    [
                        ft.Icon(
                            ft.Icons.STAR_ROUNDED,
                            size=15,
                            color=theme.rating_color(rating),
                        ),
                        ft.Text(
                            f"{rating:g}",
                            size=13,
                            color=theme.TEXT,
                            weight=ft.FontWeight.W_600,
                        ),
                    ],
                    spacing=4,
                )
            )
        if author_of(self.item):
            info.append(
                ft.Text(
                    author_of(self.item) or "",
                    size=12,
                    color=theme.MUTED,
                    max_lines=2,
                    overflow=ft.TextOverflow.ELLIPSIS,
                )
            )
        return ft.Container(
            padding=theme.pad(left=16, right=16, top=14, bottom=10),
            content=ft.Row(
                [
                    widgets.cover(cover_src, width=width),
                    ft.Column(info, spacing=8, expand=True, tight=True),
                ],
                spacing=14,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
        )

    def _facts(self) -> ft.Control:
        status = status_of(self.item) if self.item is not None else None
        note = SOURCE_NOTES.get(self.source)
        if not status and not note:
            return ft.Container()
        parts: list[str] = []
        if status:
            parts.append(status)
        if note:
            parts.append(note)
        return ft.Container(
            padding=theme.pad(left=16, right=16, bottom=8),
            content=ft.Text(" · ".join(parts), size=11.5, color=theme.MUTED),
        )

    def _genres(self) -> ft.Control:
        genres = genres_of(self.item) if self.item is not None else []
        if not genres:
            return ft.Container()
        return ft.Container(
            padding=theme.pad(left=16, right=16, bottom=10),
            content=ft.Row(
                [widgets.genre_chip(name) for name in genres[:14]],
                spacing=6,
                wrap=True,
            ),
        )

    def _actions(self) -> ft.Control:
        first = self._first_unread()
        buttons: list[ft.Control] = []
        if first is not None:
            buttons.append(
                ft.FilledButton(
                    content=ft.Text("Читать"),
                    icon=ft.Icons.PLAY_ARROW_ROUNDED,
                    bgcolor=theme.PRIMARY,
                    on_click=lambda e: self._read(first),
                )
            )
        buttons.append(
            ft.OutlinedButton(
                content=ft.Text("Все главы"),
                icon=ft.Icons.LIST_ALT,
                on_click=lambda e: self._download_all(),
            )
        )
        return ft.Container(
            padding=theme.pad(left=16, right=16, bottom=12),
            content=ft.Row(buttons, spacing=10),
        )

    def _description(self) -> ft.Control:
        text = description_of(self.item) or ""
        expanded = getattr(self, "_description_open", False)

        def toggle(e: ft.ControlEvent) -> None:
            self._description_open = not expanded
            self._render()
            self.app.refresh()

        # У Flet нельзя одновременно задавать `height` и `max_lines`: текст
        # в такой связке не рисуется вообще, поэтому высота ставится только
        # развёрнутому тексту, а обрезка — только свёрнутому.
        body = (
            ft.Text(text, size=13, color=theme.MUTED, height=1.5)
            if expanded
            else ft.Text(
                text,
                size=13,
                color=theme.MUTED,
                max_lines=4,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        )
        return ft.Container(
            padding=theme.pad(left=16, right=16, bottom=8),
            content=ft.Column(
                [
                    body,
                    ft.TextButton(
                        content=ft.Text("Свернуть" if expanded else "Читать полностью", size=12.5),
                        on_click=toggle,
                    )
                    if len(text) > 220
                    else ft.Container(),
                ],
                spacing=2,
                tight=True,
            ),
        )

    def _chapters_block(self) -> ft.Control:
        header = ft.Container(
            padding=theme.pad(left=16, right=16, top=14, bottom=8),
            content=widgets.section_header(
                "Главы",
                ft.Dropdown(
                    value=self.chapter_sort,
                    width=132,
                    dense=True,
                    filled=True,
                    fill_color=theme.SURFACE_HIGH,
                    border=ft.InputBorder.NONE,
                    border_radius=theme.radius(10),
                    text_size=12.5,
                    color=theme.TEXT,
                    options=[ft.DropdownOption(key=key, text=label) for label, key in SORTS],
                    on_select=lambda e: self._sort_chapters(e.control.value),
                ),
            ),
        )

        if self.loading_chapters and not self.chapters:
            body: ft.Control = ft.Container(
                padding=theme.pad(top=30, bottom=30),
                content=widgets.spinner("Список глав…"),
            )
        elif not self.chapters:
            body = widgets.state_view(
                ft.Icons.MENU_BOOK_OUTLINED,
                "Глав нет",
                "У этого источника список глав недоступен гостю",
            )
        else:
            states = self.state.library.chapter_states(self.source, self.key)
            tiles: list[ft.Control] = []
            for chapter in self.chapters:
                tiles.append(self._chapter_tile(chapter, states.get(chapter_key(chapter), {})))
            body = ft.Column(tiles, spacing=0, tight=True)

        return ft.Column([header, body], spacing=0, tight=True)

    def _chapter_tile(self, chapter: Any, state: dict[str, Any]) -> ft.Control:
        label = _chapter_label(chapter)
        pages = state.get("pages") or _pages_hint(chapter)
        read = bool(state.get("is_read"))
        downloaded = bool(state.get("downloaded"))
        running = self.state.downloads.running(self.source, self.key, chapter_key(chapter))

        leading: ft.Control
        if read:
            leading = ft.Icon(ft.Icons.CHECK_CIRCLE, size=19, color=theme.TERTIARY)
        else:
            leading = ft.Icon(ft.Icons.CIRCLE_OUTLINED, size=19, color=theme.BORDER)

        subtitle_parts = []
        if pages:
            subtitle_parts.append(f"{pages} стр.")
        if downloaded:
            subtitle_parts.append("скачано")

        trailing: ft.Control
        if running:
            trailing = ft.ProgressRing(width=18, height=18, stroke_width=2, color=theme.PRIMARY)
        elif downloaded:
            trailing = ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE,
                icon_size=19,
                icon_color=theme.MUTED,
                tooltip="Удалить",
                on_click=lambda e, c=chapter: self._delete_chapter(c),
            )
        else:
            trailing = ft.IconButton(
                icon=ft.Icons.DOWNLOAD_OUTLINED,
                icon_size=19,
                icon_color=theme.MUTED,
                tooltip="Скачать",
                on_click=lambda e, c=chapter: self._download(c),
            )

        return ft.Container(
            padding=theme.pad(left=16, right=8, top=4, bottom=4),
            ink=True,
            border=ft.Border(bottom=ft.BorderSide(0.5, "#1A1A22")),
            on_click=lambda e, c=chapter: self._read(c),
            content=ft.Row(
                [
                    leading,
                    ft.Column(
                        [
                            ft.Text(
                                label,
                                size=13.5,
                                color=theme.TEXT if not read else theme.MUTED,
                                weight=ft.FontWeight.W_500,
                                max_lines=2,
                            ),
                            ft.Text(
                                " · ".join(subtitle_parts),
                                size=11,
                                color=theme.MUTED,
                            )
                            if subtitle_parts
                            else ft.Container(),
                        ],
                        spacing=2,
                        expand=True,
                        tight=True,
                    ),
                    trailing,
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    # ---------- действия с главами ----------

    def _sort_chapters(self, value: str | None) -> None:
        self.chapter_sort = value or "number"
        self.chapters = sorted(self.chapters, key=self._sort_key)
        self._render()
        self.app.refresh()

    def _sort_key(self, chapter: Any) -> tuple[float, float]:
        """
        Ключ сортировки глав.

        Первая компонента — настоящий номер, вторая — позиция в выдаче сайта:
        у источников без номеров (ReManga) порядок сайта и есть порядок чтения,
        поэтому «по номеру» и «старые» совпадают, а «новые» его разворачивают.
        """
        position = float(chapter.get("_pos") or 0) if isinstance(chapter, dict) else 0.0
        value = chapter.get("number") if isinstance(chapter, dict) else None
        number = float(value) if isinstance(value, (int, float)) else position
        if self.chapter_sort == "new":
            return (-number, -position)
        return (number, position)

    def _first_unread(self) -> Any | None:
        states = self.state.library.chapter_states(self.source, self.key)
        for chapter in self.chapters:
            if not states.get(chapter_key(chapter), {}).get("is_read"):
                return chapter
        return self.chapters[0] if self.chapters else None

    def _read(self, chapter: Any) -> None:
        self.app.open_reader(self.source, self.key, self.title, self.cover, chapter, self.chapters)

    def _download(self, chapter: Any) -> None:
        client = self.state.hub.client(self.source)
        self.state.downloads.start(
            self.source,
            self.key,
            self.title,
            chapter,
            _chapter_label(chapter),
            client,
            on_done=self._render,
        )
        self._render()
        self.app.refresh()
        widgets.toast(self.app.page, "Качаем главу…", ft.Icons.DOWNLOAD_OUTLINED)

    def _download_all(self) -> None:
        client = self.state.hub.client(self.source)
        count = 0
        for chapter in self.chapters:
            uid = chapter_key(chapter)
            if self.state.downloads.running(self.source, self.key, uid):
                continue
            if self.state.library.is_downloaded(self.source, self.key, chapter):
                continue
            self.state.downloads.start(
                self.source,
                self.key,
                self.title,
                chapter,
                _chapter_label(chapter),
                client,
                on_done=self._render,
            )
            count += 1
        if count:
            widgets.toast(
                self.app.page, f"В очереди {count} глав", ft.Icons.DOWNLOAD_OUTLINED
            )
        else:
            widgets.toast(self.app.page, "Все главы уже скачаны", ft.Icons.CHECK_CIRCLE)
        self._render()
        self.app.refresh()

    def _delete_chapter(self, chapter: Any) -> None:
        self.state.images.remove_chapter(self.source, self.key, chapter)
        self.state.library.remove_download(self.source, self.key, chapter)
        self._render()
        self.app.refresh()

    def view(self) -> ft.View:
        return ft.View(
            route=f"/title/{self.source}/{self.key}",
            appbar=self.title_bar,
            controls=[self.scroll],
            padding=0,
            bgcolor=theme.BACKGROUND,
        )


def _chapter_label(chapter: Any) -> str:
    """Чем назвать главу в интерфейсе: номером, названием или позицией."""
    if not isinstance(chapter, dict):
        return str(chapter)
    number = chapter.get("number")
    title = chapter.get("title") or chapter.get("name") or chapter.get("label")
    if number is None and title is None:
        # Номера у источника нет: подписываем главу её местом в списке,
        # иначе в главе подряд идут «Глава 5006», «Глава 5822» — это
        # внутренние id, которые читателю ничего не говорят.
        position = chapter.get("_pos")
        if position:
            return f"Глава {position}"
        url = chapter.get("url")
        if url:
            return str(url).rsplit("/", 1)[-1]
        identifier = chapter.get("id")
        return f"Глава {identifier}" if identifier is not None else "Глава"
    if title:
        return f"{number} · {title}" if number is not None else str(title)
    return f"Глава {number:g}" if isinstance(number, float) else f"Глава {number}"


def _pages_hint(chapter: Any) -> int | None:
    if not isinstance(chapter, dict):
        return None
    from ...core.models import extract_pages

    pages = extract_pages(chapter)
    if pages:
        return len(pages)
    value = chapter.get("page_count") or chapter.get("pages_count")
    return value if isinstance(value, int) else None