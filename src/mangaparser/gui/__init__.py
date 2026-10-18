"""Графическое приложение на Flet: Android, iOS, десктоп.

Точка входа — `mangaparser.gui.app:main`, запускается через `flet run`
или как отдельная команда `manga-gui`.
"""

from __future__ import annotations


def run() -> None:
    """Запустить приложение в настольном окне Flet."""
    import flet as ft

    from .app import main

    ft.run(main)


__all__ = ["run"]