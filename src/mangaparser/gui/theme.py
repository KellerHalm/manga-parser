"""Оформление: AMOLED-тёмная тема и палитра.

Фон — чистый чёрный, а не тёмно-серый: на OLED-экранах Android и iOS это
даёт настоящий чёрный, экономит батарею и делает обложки единственным
источником цвета в интерфейсе.
"""

from __future__ import annotations

import flet as ft

#: Акцент по умолчанию. Тёплый розовый — как в Kotatsu, но темнее по тону.
PRIMARY = "#FF4D8D"
SECONDARY = "#7C6CFF"
TERTIARY = "#3DDC97"

BACKGROUND = "#000000"
SURFACE = "#0B0B0F"
SURFACE_HIGH = "#14141B"
SURFACE_TOP = "#1C1C25"

BORDER = "#22222E"
MUTED = "#8A8A9C"
TEXT = "#F2F2F7"

#: Шкала рейтинга: 0 -> серый, 5 -> красный.
RATING_COLORS = ("#6E6E80", "#5E9E5E", "#9DB44A", "#D2A03C", "#D4762B", "#D8443C")


def rating_color(value: float | None) -> str:
    if value is None:
        return MUTED
    return RATING_COLORS[int(max(0.0, min(5.0, float(value))))]


def rating_label(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{float(value):.1f}".removesuffix(".0")


def theme(seed: str = PRIMARY) -> ft.Theme:
    """Material 3 на чёрном фоне с заданным акцентом."""
    return ft.Theme(
        color_scheme_seed=seed,
        use_material3=True,
        scaffold_bgcolor=BACKGROUND,
        canvas_color=BACKGROUND,
        card_bgcolor=SURFACE,
        divider_color=BORDER,
        splash_color="white12",
        highlight_color="white10",
        card_theme=ft.CardTheme(
            color=SURFACE_HIGH,
            elevation=0,
            shape=ft.RoundedRectangleBorder(radius=18),
        ),
        appbar_theme=ft.AppBarTheme(
            bgcolor=BACKGROUND,
            elevation=0,
            center_title=False,
            title_text_style=ft.TextStyle(
                size=19, weight=ft.FontWeight.W_600, color=TEXT, letter_spacing=0.1
            ),
        ),
        navigation_bar_theme=ft.NavigationBarTheme(
            bgcolor=SURFACE_TOP,
            elevation=0,
            indicator_color=f"{seed}33",
            label_text_style=ft.TextStyle(size=11, weight=ft.FontWeight.W_500),
        ),
        navigation_rail_theme=ft.NavigationRailTheme(
            bgcolor=BACKGROUND,
            indicator_color=f"{seed}33",
            label_type=ft.NavigationRailLabelType.ALL,
            unselected_label_text_style=ft.TextStyle(size=12, color=MUTED),
            selected_label_text_style=ft.TextStyle(size=12, color=TEXT),
        ),
        chip_theme=ft.ChipTheme(
            bgcolor=SURFACE_HIGH,
            label_text_style=ft.TextStyle(size=13, color=TEXT),
            border_side=ft.BorderSide(1, BORDER),
            shape=ft.RoundedRectangleBorder(radius=20),
            show_checkmark=False,
        ),
        dialog_theme=ft.DialogTheme(bgcolor=SURFACE_HIGH),
        snackbar_theme=ft.SnackBarTheme(
            bgcolor=SURFACE_TOP,
            content_text_style=ft.TextStyle(color=TEXT),
            behavior=ft.SnackBarBehavior.FLOATING,
            shape=ft.RoundedRectangleBorder(radius=14),
        ),
        progress_indicator_theme=ft.ProgressIndicatorTheme(color=seed),
        text_theme=ft.TextTheme(
            body_large=ft.TextStyle(size=16, color=TEXT, height=1.45),
            body_medium=ft.TextStyle(size=14.5, color=TEXT, height=1.4),
            body_small=ft.TextStyle(size=12.5, color=MUTED, height=1.35),
            label_large=ft.TextStyle(size=14, weight=ft.FontWeight.W_500),
            label_small=ft.TextStyle(size=11.5, color=MUTED),
            headline_small=ft.TextStyle(size=21, weight=ft.FontWeight.W_600, color=TEXT),
            title_large=ft.TextStyle(size=18, weight=ft.FontWeight.W_600, color=TEXT),
            title_medium=ft.TextStyle(size=16, weight=ft.FontWeight.W_500, color=TEXT),
        ),
        scrollbar_theme=ft.ScrollbarTheme(thumb_color=BORDER, thickness=4, radius=8),
    )


def apply(page: ft.Page, *, accent_color: str = PRIMARY) -> None:
    """Включить AMOLED-тему на странице."""
    built = theme(accent_color)
    page.theme = built
    page.dark_theme = built
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = BACKGROUND


def fade(content: ft.Control, duration: int = 200) -> ft.AnimatedSwitcher:
    """Мягкая смена содержимого: список сменился источником, папка открылась."""
    return ft.AnimatedSwitcher(
        content=content,
        duration=ft.Duration(milliseconds=duration),
        switch_in_curve=ft.AnimationCurve.EASE_OUT,
        switch_out_curve=ft.AnimationCurve.EASE_IN,
    )


def cover_shade() -> ft.Paint:
    """Затемнение под подписью поверх обложки.

    Обложки светлые, пёстрые и часто с белым текстом поверх рисунка, поэтому
    низ уходит почти в чёрный раньше, чем заканчивается картинка: иначе
    название тайтла не прочитать поверх светлой обложки.
    """
    return ft.Paint(
        gradient=ft.LinearGradient(
            begin=ft.Alignment.TOP_CENTER,
            end=ft.Alignment.BOTTOM_CENTER,
            colors=[
                "rgba(0,0,0,0)",
                "rgba(0,0,0,0.35)",
                "rgba(0,0,0,0.82)",
                "rgba(0,0,0,0.97)",
            ],
            stops=[0.28, 0.55, 0.78, 1.0],
        )
    )


def fade_in() -> ft.AnimatedSwitcher:
    return ft.AnimatedSwitcher(
        duration=ft.Duration(milliseconds=160),
        switch_in_curve=ft.AnimationCurve.EASE_OUT,
    )


# ---------- мелочи раскладки ----------
#
# В Flet 1.0 отступы и скругления задаются полными конструкторами
# (`Padding(left=...)`, `BorderRadius(top_left=...)`), поэтому короткие
# помощники: иначе каждый отступ в коде занимает строку.


def pad(
    *,
    top: float = 0,
    bottom: float = 0,
    left: float = 0,
    right: float = 0,
    all: float | None = None,
) -> ft.Padding:
    if all is not None:
        return ft.Padding(left=all, top=all, right=all, bottom=all)
    return ft.Padding(left=left, top=top, right=right, bottom=bottom)


def radius(value: float) -> ft.BorderRadius:
    return ft.BorderRadius(top_left=value, top_right=value, bottom_left=value, bottom_right=value)


def line(width: float = 1, color: str = BORDER) -> ft.Border:
    return ft.Border.all(width, color)


#: У `ft.Text` нельзя одновременно задавать `height` и `max_lines`: такой текст
#: не рисуется вообще (нулевая высота). Либо межстрочный интервал, либо
#: ограничение строк — но не оба сразу.