"""Выбор источника: плитки сайтов с их особенностями.

Ограничения источников видны до того, как пользователь откроет каталог:
у Com-X нет глав, у Desu.Me открыта первая глава тома. Иначе пустой
список глав выглядит как поломка приложения.
"""

from __future__ import annotations

from typing import Any

import flet as ft

from .. import theme, widgets
from ..state import SOURCE_NOTES, AppState, label_of


class SourcesView:
    """Сетка источников: тап -> каталог этого сайта."""

    def __init__(self, app: Any, state: AppState) -> None:
        self.app = app
        self.state = state
        self.appbar = ft.AppBar(
            title=ft.Text("Обзор"),
            bgcolor=theme.BACKGROUND,
        )
        self.grid = ft.GridView(
            expand=True,
            max_extent=230,
            spacing=12,
            run_spacing=12,
            padding=theme.pad(left=16, right=16, top=12, bottom=96),
            child_aspect_ratio=1.35,
        )

    def load(self) -> None:
        self.grid.controls = [
            self._tile(name, label_of(name), SOURCE_NOTES.get(name))
            for name in self.state.ordered_sources()
        ]

    def _tile(self, name: str, title: str, note: str | None) -> ft.Container:
        tint = widgets.source_tint(name)
        return ft.Container(
            bgcolor=theme.SURFACE_HIGH,
            border_radius=theme.radius(18),
            border=theme.line(1, tint + "33"),
            padding=theme.pad(left=16, top=16, right=16, bottom=16),
            ink=True,
            on_click=lambda e: self.app.navigate(f"/explore/{name}"),
            content=ft.Column(
                [
                    ft.Container(
                        width=42,
                        height=42,
                        border_radius=theme.radius(12),
                        bgcolor=tint + "22",
                        alignment=ft.Alignment.CENTER,
                        content=ft.Text(
                            name[0].upper(),
                            size=19,
                            weight=ft.FontWeight.W_700,
                            color=tint,
                        ),
                    ),
                    ft.Text(
                        title,
                        size=15.5,
                        weight=ft.FontWeight.W_600,
                        color=theme.TEXT,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    ft.Text(
                        note or "Каталог, карточка и главы",
                        size=11.5,
                        color=theme.MUTED,
                        max_lines=2,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
                spacing=9,
                alignment=ft.MainAxisAlignment.CENTER,
                horizontal_alignment=ft.CrossAxisAlignment.START,
                tight=True,
            ),
        )

    def body_view(self) -> ft.GridView:
        self.load()
        return self.grid

    def view(self) -> ft.View:
        self.load()
        return ft.View(
            route="/explore",
            appbar=self.appbar,
            controls=[self.grid],
            padding=0,
            bgcolor=theme.BACKGROUND,
        )