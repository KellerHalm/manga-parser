"""Читалка: два режима, офлайн и прогресс.

* «Вебтунс» — вертикальный поток страниц, как на сайтах с вебтунами.
* «Страницы» — постраничный режим с RTL-листанием: вправо — назад,
  влево — вперёд, как в бумажной манге.

Страницы идут через кэш на диске, а не напрямую по сети: CDN источников
отдают картинки только с `Referer` сайта, а из кэша они открываются
мгновенно и без интернета. Глава докачивается в фоне, а экран показывает
уже готовые страницы — поэтому на медленной сети чтение начинается сразу.

Прогресс пишется в БД на каждой странице и по выходе из экрана, поэтому
книга открывается там же, где её оставили.
"""

from __future__ import annotations

import threading
from typing import Any

import flet as ft

from .. import theme, widgets
from ..state import AppState, label_of
from ..store import chapter_key

PRELOAD = 4  # сколько страниц тянуть вперёд сразу
SAVE_EVERY = 4  # писать прогресс не на каждой странице, а раз в N
PLACEHOLDER_HEIGHT = 420


class ReaderView:
    """Экран чтения одной главы."""

    #: Экран перерисовывает себя сам: пришедшие страницы подставляются
    #: прямо в контейнеры, пересборка `View` сбивала бы положение страницы.
    manages_view = True

    def __init__(
        self,
        app: Any,
        state: AppState,
        source: str,
        key: str,
        title: str,
        cover: str | None,
        chapter: Any,
        chapters: list[Any],
    ) -> None:
        self.app = app
        self.state = state
        self.source = source
        self.key = key
        self.title = title
        self.cover = cover
        self.chapter = chapter
        self.chapters = list(chapters)
        self.index = self._chapter_index()

        self.mode = state.reader_mode
        self.urls: list[str] = []
        self.files: list[str | None] = []
        self.offline = False
        self.current = 0
        self.loading = True
        self.loading_more = False
        self.error: str | None = None
        self.controls_visible = True

        self._controls: list[ft.Container] = []
        self._list: ft.ListView | None = None
        self._pager: ft.PageView | None = None
        self._stop = threading.Event()

        self.container = ft.Container(expand=True, bgcolor="#000000")
        self.slider = ft.Slider(
            min=0,
            max=1,
            value=0,
            active_color=theme.PRIMARY,
            inactive_color=theme.SURFACE_TOP,
            on_change=self._on_slider,
        )
        self.page_label = ft.Text("", size=12, color="#B9B9C6")
        self.top_bar = ft.Container(
            alignment=ft.Alignment.TOP_CENTER,
            bgcolor="rgba(0,0,0,0.9)",
            padding=theme.pad(left=4, right=4, top=4, bottom=4),
            content=ft.Row(
                [
                    ft.IconButton(
                        icon=ft.Icons.ARROW_BACK,
                        icon_color="#FFFFFF",
                        on_click=lambda e: self._close(),
                    ),
                    ft.Column(
                        [
                            ft.Text(
                                self.title,
                                size=13.5,
                                weight=ft.FontWeight.W_600,
                                color="#FFFFFF",
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Text(
                                _chapter_label(self.chapter),
                                size=11,
                                color="#B9B9C6",
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                        ],
                        spacing=2,
                        expand=True,
                        tight=True,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.SWAP_VERT,
                        icon_color="#FFFFFF",
                        tooltip="Сменить режим",
                        on_click=lambda e: self._toggle_mode(),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.DOWNLOAD_OUTLINED,
                        icon_color="#FFFFFF",
                        tooltip="Скачать",
                        on_click=lambda e: self._download(),
                    ),
                ],
                spacing=2,
            ),
        )
        self.bottom_bar = ft.Container(
            alignment=ft.Alignment.BOTTOM_CENTER,
            bgcolor="rgba(0,0,0,0.9)",
            padding=theme.pad(left=14, right=14, top=6, bottom=10),
            content=ft.Column(
                [
                    self.slider,
                    ft.Row(
                        [
                            ft.IconButton(
                                icon=ft.Icons.CHEVRON_RIGHT,
                                icon_color="#FFFFFF",
                                tooltip="Предыдущая",
                                on_click=lambda e: self._step(-1),
                            ),
                            ft.Container(content=self.page_label, expand=True),
                            ft.IconButton(
                                icon=ft.Icons.CHEVRON_LEFT,
                                icon_color="#FFFFFF",
                                tooltip="Следующая",
                                on_click=lambda e: self._step(1),
                            ),
                        ],
                        spacing=4,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=2,
                tight=True,
            ),
        )

    # ---------- загрузка главы ----------

    def load(self) -> None:
        """Скачанная глава читается с диска, иначе список страниц из сети."""
        local = self.state.images.local_chapter(self.source, self.key, self.chapter)
        if local:
            self.offline = True
            self.urls = list(local)
            self.files = list(local)
            self.loading = False
            self._after_load()
            return

        client = self.state.hub.client(self.source)

        def worker() -> None:
            try:
                urls = [str(u) for u in client.chapter_pages(self.key, self.chapter) if u]
                if not urls:
                    raise RuntimeError(
                        "У этой главы нет доступных картинок на сайте источника"
                    )
                self.urls = urls
                self.files = [None] * len(urls)
            except Exception as exc:
                self.error = str(exc)
                self.urls = []
            finally:
                self.loading = False
                self._after_load()
                self.app.refresh()

        self.app.page.run_thread(worker)

    def _after_load(self) -> None:
        if not self.urls:
            self._render()
            return
        self._build_controls()
        self._resume()
        self._render()
        self._prefetch()

    def _resume(self) -> None:
        """Вернуться на страницу, на которой остановились в прошлый раз."""
        progress = self.state.library.progress(self.source, self.key)
        if not progress or progress.get("chapter_key") != chapter_key(self.chapter):
            return
        page = int(progress.get("page") or 0)
        self.current = max(0, min(page, len(self.urls) - 1))

    def _build_controls(self) -> None:
        """Контейнер на каждую страницу: место под картинку и загрузчик."""
        self._controls = [self._page_box(index) for index in range(len(self.urls))]

    def _page_box(self, index: int) -> ft.Container:
        # Без `alignment`: в вертикальном списке высота не ограничена, и
        # контейнер с выравниванием схлопывается в ноль — страница не видна.
        # Заглушка выровнена сама по себе.
        box = ft.Container(
            key=f"page-{index}",
            content=_placeholder(),
            bgcolor="#000000",
        )
        if index < len(self.files) and self.files[index]:
            self._fill(box, self.files[index])
        return box

    def _fill(self, box: ft.Container, path: str) -> None:
        box.bgcolor = None
        if self.mode == "webtoon":
            box.content = ft.GestureDetector(
                content=ft.Image(src=path, fit=ft.BoxFit.FIT_WIDTH, gapless_playback=True),
                on_tap=self._tap,
            )
        else:
            box.content = ft.GestureDetector(
                content=ft.InteractiveViewer(
                    content=ft.Image(src=path, fit=ft.BoxFit.CONTAIN),
                    scale_enabled=True,
                    pan_enabled=True,
                    max_scale=5,
                ),
                on_tap=self._tap,
            )

    # ---------- докачка страниц ----------

    def _prefetch(self) -> None:
        """Тянуть страницы в фоне, начиная с текущей и вперёд."""
        if self.offline or self.loading_more:
            return
        self.loading_more = True
        self._stop.clear()
        order = list(range(self.current, len(self.urls)))
        order += list(range(0, self.current))
        state = self.state
        source = self.source

        def worker() -> None:
            ready = 0
            for index in order:
                if self._stop.is_set():
                    break
                if self.files[index]:
                    continue
                path = state.page_image(self.urls[index], source)
                if path:
                    self.files[index] = path
                    ready += 1
                if ready >= PRELOAD:
                    ready = 0
                    self._fill(self._controls[index], path or "")
                    self._sync()
                    self.app.refresh()
            self._fill_pending()
            self.loading_more = False
            self.app.refresh()

        # Через `run_thread`, а не через свой поток: из своего потока
        # обновления до клиента не доходят, и подставленные страницы
        # остаются невидимыми.
        self.app.page.run_thread(worker)

    def _fill_pending(self) -> None:
        """Подставить всё, что скачалось, — после паузы или смены режима."""
        for index, path in enumerate(self.files):
            if path and not _has_image(self._controls[index]):
                self._fill(self._controls[index], path)
        self._sync()
        self.app.refresh()

    def _sync(self) -> None:
        """
        Пересобрать дочерние контролы списка или пейджера.

        Flet не перестраивает элемент списка, если у него поменялось только
        содержимое: подставленная картинка осталась бы невидимой, пока у
        контейнера не изменится ссылка целиком. Поэтому список пересобирается
        заново — дёшево, страницы это те же самые объекты.
        """
        if self._list is not None:
            self._list.controls = list(self._controls)
        if self._pager is not None:
            self._pager.controls = list(self._controls)

    # ---------- режимы ----------

    def _toggle_mode(self) -> None:
        self.state.toggle_reader_mode()
        self.mode = self.state.reader_mode
        widgets.toast(
            self.app.page, "Режим: " + self.state.reader_mode_label(), ft.Icons.SWAP_VERT
        )
        for index, path in enumerate(self.files):
            if path:
                self._fill(self._controls[index], path)
        self._sync()
        self._render()
        self.app.refresh()

    # ---------- навигация по страницам ----------

    def _step(self, delta: int) -> None:
        target = self.current + delta
        if target < 0:
            self._open_neighbour(-1)
            return
        if target >= len(self.urls):
            self._open_neighbour(1)
            return
        self.current = target
        self._render()
        self._prefetch()
        self._save()

    def _chapter_index(self) -> int:
        wanted = chapter_key(self.chapter)
        for index, chapter in enumerate(self.chapters):
            if chapter_key(chapter) == wanted:
                return index
        return 0

    def _open_neighbour(self, delta: int) -> None:
        target = self.index + delta
        if not 0 <= target < len(self.chapters):
            widgets.toast(
                self.app.page,
                "Это последняя глава" if delta > 0 else "Это первая глава",
                ft.Icons.INFO_OUTLINE,
            )
            return
        self.app.open_reader(
            self.source,
            self.key,
            self.title,
            self.cover,
            self.chapters[target],
            self.chapters,
            replace=True,
        )

    def _on_slider(self, e: ft.ControlEvent) -> None:
        target = int(e.control.value or 0)
        if target == self.current:
            return
        self.current = max(0, min(target, len(self.urls) - 1))
        # Счётчик страниц обновляем здесь же: иначе после перетаскивания
        # ползунка он продолжает показывать прежнюю страницу.
        self.page_label.value = f"стр. {self.current + 1} из {len(self.urls)}"
        self._scroll_to(self.current)
        self._save()

    def _scroll_to(self, index: int) -> None:
        if self._pager is not None:
            self._pager.selected_index = index
        elif self._list is not None:
            # Прокрутка к конкретной странице — запрос к клиенту, и он может
            # не ответить (страница ещё не построена, окно свернуто). Ошибка
            # здесь не должна ронять обработчик: страница всё равно сменится.
            self.app.page.run_task(self._jump_to, index)

    async def _jump_to(self, index: int) -> None:
        if self._list is None:
            return
        try:
            await self._list.scroll_to(scroll_key=f"page-{index}", duration=120)
        except Exception:
            return

    def _save(self, force: bool = False) -> None:
        if not self.urls:
            return
        last = self.current == len(self.urls) - 1
        if not force and not last and self.current % SAVE_EVERY:
            return
        self.state.library.save_progress(
            self.source,
            self.key,
            self.title,
            self.chapter,
            _chapter_label(self.chapter),
            self.current,
            len(self.urls),
            self.cover,
        )
        if last:
            self.state.library.mark_chapter_read(
                self.source, self.key, self.chapter, len(self.urls)
            )

    def _download(self) -> None:
        client = self.state.hub.client(self.source)
        self.state.downloads.start(
            self.source,
            self.key,
            self.title,
            self.chapter,
            _chapter_label(self.chapter),
            client,
            pages=None if self.offline else self.urls,
            on_done=lambda: self.app.refresh(),
        )
        widgets.toast(self.app.page, "Качаем главу…", ft.Icons.DOWNLOAD_OUTLINED)

    def _close(self) -> None:
        self._stop.set()
        self._save(force=True)
        self.app.back()

    def _tap(self, e: ft.TapEvent) -> None:
        """Тап по картинке: края листают, середина прячет панели."""
        width = self.app.page.width or 400
        position = e.local_position.x if e.local_position else width / 2
        ratio = position / width
        if ratio < 0.3:
            self._step(1)
        elif ratio > 0.7:
            self._step(-1)
        else:
            self.controls_visible = not self.controls_visible
            self._render()
            self.app.refresh()

    # ---------- отрисовка ----------

    def _render(self) -> None:
        if self.loading:
            self.container.content = ft.Container(
                expand=True, alignment=ft.Alignment.CENTER, content=widgets.spinner("Глава…")
            )
        elif self.error or not self.urls:
            self.container.content = widgets.state_view(
                ft.Icons.BROKEN_IMAGE_OUTLINED,
                "Глава не открылась",
                _short(self.error or "Источник не отдал страницы"),
                ft.FilledButton(content=ft.Text("Повторить"), on_click=lambda e: self._retry()),
            )
        elif self.mode == "webtoon":
            self._render_webtoon()
        else:
            self._render_paged()

        self.slider.max = max(len(self.urls) - 1, 1)
        self.slider.value = min(self.current, max(len(self.urls) - 1, 1))
        self.page_label.value = (
            f"стр. {self.current + 1} из {len(self.urls)} · {label_of(self.source)}"
        )
        self.top_bar.visible = self.controls_visible
        self.bottom_bar.visible = self.controls_visible

    def _render_webtoon(self) -> None:
        if self._list is None:
            self._list = ft.ListView(
                expand=True,
                spacing=0,
                padding=0,
                controls=self._controls,
                on_scroll=self._on_webtoon_scroll,
                scroll_interval=150,
            )
        self.container.content = self._list

    def _render_paged(self) -> None:
        if self._pager is None:
            self._pager = ft.PageView(
                expand=True,
                controls=self._controls,
                selected_index=0,
                # RTL: страницы идут справа налево, как в бумажной манге —
                # свайп вправо открывает следующую, влево — предыдущую.
                reverse=True,
                on_change=self._on_page_change,
            )
        self._pager.selected_index = min(self.current, len(self._controls) - 1)
        self.container.content = self._pager

    def _retry(self) -> None:
        self.loading = True
        self.error = None
        self._render()
        self.app.refresh()
        self.load()

    def _on_webtoon_scroll(self, e: ft.OnScrollEvent) -> None:
        """Текущая страница по прокрутке: она нужна для прогресса и слайдера."""
        if not self.urls or not e.viewport_dimension:
            return
        per_page = max(e.viewport_dimension * 1.5, 1.0)
        page = max(0, min(int(e.pixels / per_page), len(self.urls) - 1))
        if page != self.current:
            self.current = page
            self.slider.value = page
            self.page_label.value = f"стр. {page + 1} из {len(self.urls)}"
            self._save()

    def _on_page_change(self, e: ft.ControlEvent) -> None:
        self.current = int(e.control.selected_index)
        self.slider.value = self.current
        self.page_label.value = f"стр. {self.current + 1} из {len(self.urls)}"
        self._prefetch()
        self._save()

    def view(self) -> ft.View:
        return ft.View(
            route=f"/read/{self.source}/{self.key}",
            controls=[
                ft.SafeArea(
                    expand=True,
                    content=ft.Stack(
                        [self.container, self.top_bar, self.bottom_bar],
                        expand=True,
                    ),
                )
            ],
            padding=0,
            bgcolor="#000000",
        )


def _placeholder() -> ft.Control:
    return ft.Container(
        height=PLACEHOLDER_HEIGHT,
        alignment=ft.Alignment.CENTER,
        content=ft.ProgressRing(width=18, height=18, stroke_width=2, color="#2A2A34"),
    )


def _has_image(box: ft.Container) -> bool:
    content = box.content
    return isinstance(content, ft.GestureDetector) and content.content is not None


def _chapter_label(chapter: Any) -> str:
    """Чем назвать главу в интерфейсе: номером, названием или позицией."""
    if not isinstance(chapter, dict):
        return str(chapter)
    number = chapter.get("number")
    title = chapter.get("title") or chapter.get("name") or chapter.get("label")
    if title:
        return f"{number} · {title}" if number is not None else str(title)
    if number is not None:
        return f"Глава {number:g}" if isinstance(number, float) else f"Глава {number}"
    position = chapter.get("_pos")
    if position:
        return f"Глава {position}"
    return "Глава"


def _short(text: str, limit: int = 110) -> str:
    text = str(text).replace("\n", " ")
    return text if len(text) <= limit else f"{text[:limit]}…"