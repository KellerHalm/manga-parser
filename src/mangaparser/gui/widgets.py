"""Общие элементы интерфейса: обложки, карточки, чипы, состояния.

Правило одно: обложка всегда одинаковой формы (2:3) и всегда со своим
состоянием загрузки, иначе сетка «прыгает» при подстановке картинок.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import flet as ft

from . import theme
from .hub import cover_of, rating_of, title_of
from .state import label_of
from .theme import rating_label

COVER_RATIO = 0.68  # ширина/высота обложки, как у большинства тайтлов

#: Сколько обложек качается одновременно: сетка на 30 тайтлов иначе
#: отправит 30 запросов разом и упрётся в пул потоков и в rate-limit сайта.
_COVER_SLOTS = threading.BoundedSemaphore(6)

SOURCE_TINTS = {
    "remanga": "#FF4D8D",
    "mangalib": "#4D9BFF",
    "buzumanga": "#FFB020",
    "inkstory": "#3DDC97",
    "selfmanga": "#7C6CFF",
    "desu": "#FF6B4D",
    "comx": "#8A8A9C",
}


def source_tint(source: str) -> str:
    return SOURCE_TINTS.get(source, theme.MUTED)


def skeleton(width: float, height: float, radius: float = 12) -> ft.Container:
    """Заглушка на время загрузки обложки."""
    return ft.Container(
        width=width,
        height=height,
        border_radius=theme.radius(radius),
        bgcolor=theme.SURFACE_HIGH,
        alignment=ft.Alignment.CENTER,
        animate_opacity=300,
        animate=ft.Animation(700, ft.AnimationCurve.EASE_IN_OUT),
    )


def cover(
    src: str | None,
    *,
    width: float = 140,
    radius: float = 14,
    fit: ft.BoxFit = ft.BoxFit.COVER,
) -> ft.Control:
    """Обложка с местом под заглушку: сетка не прыгает, пока грузится."""
    if not src:
        return placeholder(width, width / COVER_RATIO, radius)
    return ft.Image(
        src=src,
        width=width,
        height=width / COVER_RATIO,
        fit=fit,
        border_radius=theme.radius(radius),
        error_content=placeholder(width, width / COVER_RATIO, radius),
        fade_in_animation=ft.Animation(220, ft.AnimationCurve.EASE_OUT),
    )


def placeholder(width: float, height: float, radius: float = 14) -> ft.Control:
    return ft.Container(
        width=width,
        height=height,
        border_radius=theme.radius(radius),
        bgcolor=theme.SURFACE_HIGH,
        alignment=ft.Alignment.CENTER,
        content=ft.Icon(ft.Icons.IMAGE_NOT_SUPPORTED_OUTLINED, size=26, color=theme.BORDER),
    )


def lazy_cover(
    page: ft.Page,
    state: Any,
    url: str,
    box: ft.Container,
    width: float,
    source: str | None = None,
) -> None:
    """
    Подставить обложку в контейнер: из кэша сразу, иначе скачать в фоне.

    Сначала проверяется кэш (это чтение с диска), сеть трогается только
    отдельным потоком — иначе отрисовка сетки встала бы на каждый тайтл.
    """
    if not url:
        return
    source = source or ""
    cached = state.images.cached_cover(url)
    if cached:
        box.content = cover(cached, width=width)
        return

    def worker() -> None:
        with _COVER_SLOTS:
            try:
                path = state.images.fetch_cover(url, state.referer(source) if source else None)
            except Exception:
                return
        if path:
            box.content = cover(path, width=width)
            page.update(box)

    # Через `run_thread`, а не свой поток: из чужого потока обновления до
    # клиента не доходят и картинка остаётся заглушкой навсегда.
    page.run_thread(worker)


def rating_badge(value: float | None) -> ft.Container:
    text = rating_label(value)
    if value is None:
        return ft.Container()
    return ft.Container(
        padding=theme.pad(left=7, right=7, top=3, bottom=3),
        border_radius=theme.radius(8),
        bgcolor="rgba(0,0,0,0.66)",
        content=ft.Row(
            [
                ft.Icon(ft.Icons.STAR_ROUNDED, size=13, color=theme.rating_color(value)),
                ft.Text(
                    text,
                    size=11.5,
                    weight=ft.FontWeight.W_600,
                    color="#FFFFFF",
                ),
            ],
            spacing=3,
            tight=True,
        ),
    )


def source_dot(source: str) -> ft.Container:
    return ft.Container(
        width=8,
        height=8,
        border_radius=theme.radius(4),
        bgcolor=source_tint(source),
    )


def genre_chip(text: str, selected: bool = False) -> ft.Container:
    """Жанр как капсула: не `Chip`, потому что у него лишние состояния."""
    return ft.Container(
        padding=theme.pad(left=11, right=11, top=5, bottom=5),
        border_radius=theme.radius(20),
        bgcolor=theme.PRIMARY if selected else theme.SURFACE_HIGH,
        border=theme.line(1, theme.PRIMARY if selected else theme.BORDER),
        content=ft.Text(
            text,
            size=12.5,
            color="#FFFFFF" if selected else theme.TEXT,
            weight=ft.FontWeight.W_500,
        ),
    )


def section_header(title: str, trailing: ft.Control | None = None) -> ft.Row:
    controls: list[ft.Control] = [
        ft.Text(title, size=15, weight=ft.FontWeight.W_600, color=theme.TEXT)
    ]
    if trailing:
        controls.append(trailing)
    return ft.Row(
        controls,
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def cover_card(
    item: Any,
    *,
    source: str,
    width: float = 150,
    on_click: Callable[[Any], None] | None = None,
    on_favorite: Callable[[Any], None] | None = None,
    is_favorite: bool = False,
    subtitle: str | None = None,
    show_source: bool = True,
    state: Any = None,
    page: ft.Page | None = None,
) -> ft.Container:
    """
    Плитка каталога: обложка, название, источник и рейтинг.

    Подпись лежит поверх затемнения внизу обложки — так она читается на любой
    картинке, а сетка остаётся плотной.

    `show_source=False` убирает подпись с источником: в результатах поиска
    источник и так написан заголовком блока, а на плитке он только шумит.

    Если переданы `state` и `page`, обложка тянется через кэш в фоне: сначала
    показывается заглушка, потом картинка. Само по себе Flet обложку не
    покажет — CDN источников отдают её только с `Referer` сайта.
    """
    title = title_of(item)
    rating = rating_of(item)
    cover_src = cover_of(item)
    picture = ft.Container(content=cover(None, width=width), width=width)

    if state is not None and page is not None and cover_src:
        lazy_cover(page, state, cover_src, picture, width, source)

    caption: list[ft.Control] = []
    if show_source or subtitle:
        caption.append(
            ft.Text(
                subtitle or label_of(source),
                size=10.5,
                color=source_tint(source),
                weight=ft.FontWeight.W_700,
                no_wrap=True,
            )
        )
    caption.append(
        ft.Text(
            title,
            size=12.5,
            weight=ft.FontWeight.W_600,
            color="#FFFFFF",
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
    )

    stack = ft.Stack(
        [
            picture,
            ft.Container(
                content=theme.cover_shade(),
                width=width,
                height=width / COVER_RATIO,
                border_radius=theme.radius(14),
            ),
            ft.Container(
                width=width,
                height=width / COVER_RATIO,
                border_radius=theme.radius(14),
                padding=theme.pad(left=9, right=9, top=8, bottom=14),
                alignment=ft.Alignment.BOTTOM_LEFT,
                content=ft.Column(caption, spacing=2, tight=True),
            ),
            ft.Container(
                padding=theme.pad(left=6, top=6),
                alignment=ft.Alignment.TOP_LEFT,
                content=rating_badge(rating),
            ),
        ],
        width=width,
        height=width / COVER_RATIO,
    )

    if on_favorite:
        stack.controls.append(
            ft.Container(
                alignment=ft.Alignment.TOP_RIGHT,
                padding=theme.pad(right=4, top=4),
                content=ft.IconButton(
                    icon=ft.Icons.FAVORITE if is_favorite else ft.Icons.FAVORITE_BORDER,
                    icon_size=19,
                    icon_color=theme.PRIMARY if is_favorite else "#FFFFFF",
                    bgcolor="rgba(0,0,0,0.5)",
                    on_click=lambda e: on_favorite(item),
                ),
            )
        )

    return ft.Container(
        content=stack,
        width=width,
        on_click=(lambda e: on_click(item)) if on_click else None,
        ink=True,
        border_radius=theme.radius(14),
    )


def state_view(
    icon: Any,
    title: str,
    hint: str | None = None,
    action: ft.Control | None = None,
) -> ft.Container:
    """Пусто / ошибка / нет сети — с подсказкой, что делать дальше."""
    children: list[ft.Control] = [
        ft.Icon(icon, size=42, color=theme.BORDER),
        ft.Text(title, size=15, color=theme.MUTED, weight=ft.FontWeight.W_500),
    ]
    if hint:
        children.append(
            ft.Text(
                hint,
                size=12.5,
                color=theme.MUTED,
                text_align=ft.TextAlign.CENTER,
                max_lines=3,
            )
        )
    if action:
        children.append(action)
    return ft.Container(
        expand=True,
        alignment=ft.Alignment.CENTER,
        padding=theme.pad(left=32, right=32, top=40, bottom=40),
        content=ft.Column(
            children,
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        ),
    )


def spinner(text: str | None = None) -> ft.Control:
    if not text:
        return ft.ProgressRing(width=26, height=26, stroke_width=2.4, color=theme.PRIMARY)
    return ft.Row(
        [
            ft.ProgressRing(width=18, height=18, stroke_width=2.2, color=theme.PRIMARY),
            ft.Text(text, size=13, color=theme.MUTED),
        ],
        spacing=10,
        alignment=ft.MainAxisAlignment.CENTER,
    )


def progress_row(done: int, total: int) -> ft.Row:
    value = (done / total) if total else 0
    return ft.Row(
        [
            ft.ProgressBar(value=value, color=theme.PRIMARY, bgcolor=theme.SURFACE_TOP, height=4),
            ft.Text(f"{done}/{total}", size=11.5, color=theme.MUTED),
        ],
        spacing=10,
        alignment=ft.MainAxisAlignment.CENTER,
    )


def toast(page: ft.Page, message: str, icon: Any = ft.Icons.INFO_OUTLINE) -> None:
    """Короткое уведомление поверх экрана."""
    page.show_dialog(
        ft.SnackBar(
            content=ft.Row(
                [
                    ft.Icon(icon, size=17, color=theme.PRIMARY),
                    ft.Text(message, size=13.5, color=theme.TEXT, expand=True),
                ],
                spacing=10,
            ),
            duration=ft.Duration(milliseconds=2400),
        )
    )


def confirm_dialog(
    title: str,
    message: str,
    on_confirm: Callable[[], None],
    confirm_label: str = "Удалить",
) -> ft.AlertDialog:
    def handle(e: ft.Event) -> None:
        page = e.page
        page.pop_dialog()
        on_confirm()

    return ft.AlertDialog(
        title=ft.Text(title, size=17, weight=ft.FontWeight.W_600),
        content=ft.Text(message, size=14, color=theme.MUTED),
        actions=[
            ft.TextButton(content="Отмена", on_click=lambda e: e.page.pop_dialog()),
            ft.FilledButton(
                content=ft.Text(confirm_label),
                bgcolor="#D8443C",
                on_click=handle,
            ),
        ],
    )