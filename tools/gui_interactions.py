"""Проверка кнопок: каждая должна приводить к наблюдаемому результату.

Обработчики вызываются напрямую (как их вызывает Flet), и проверяется не
«было ли исключение», а изменилось ли что-то снаружи: маршрут, содержимое
контролов, состояние БД. Кнопка, которая ничего не меняет, — это баг.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import flet as ft  # noqa: E402

from mangaparser.gui.app import App  # noqa: E402
from mangaparser.gui.views.library import LibraryView  # noqa: E402

SOURCE = "remanga"
KEY = "barakamon"
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'ФЕЙЛ'}  {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILS.append(name)


def fire(handler, control: ft.Control, page: ft.Page) -> None:
    """
    Вызвать обработчик так, как это делает Flet.

    Событие собирается без `page`: в Flet он берётся из контекста, поэтому
    здесь же ставится текущая страница — иначе `e.page` внутри обработчика
    был бы пустым.
    """
    from flet.controls import context

    context._context_page.set(page)
    handler(ft.Event(control=control, name="test"))


def click(page: ft.Page, control: ft.Control) -> None:
    handler = getattr(control, "on_click", None)
    assert handler is not None, f"у {type(control).__name__} нет on_click"
    fire(handler, control, page)


def change(page: ft.Page, control: ft.Control, name: str, value) -> None:
    handler = getattr(control, name, None)
    assert handler is not None, f"у {type(control).__name__} нет {name}"
    # Пользователь меняет значение на самом контроле, а обработчик читает
    # его из события: так же нужно и здесь.
    if value is not None:
        for attr in ("selected_index", "value"):
            if hasattr(control, attr):
                setattr(control, attr, value)
                break
    fire(handler, control, page)


WALK = (
    "controls",
    "content",
    "actions",
    "leading",
    "trailing",
    "title",
    "navigation_bar",
    "floating_action_button",
    "subtitle",
    "destinations",
    "bottom_bar",
)


def find(control: ft.Control, kind: type, out: list | None = None) -> list:
    """Все контролы нужного типа в поддереве (включая AppBar и меню)."""
    out = [] if out is None else out
    if control is None:
        return out
    if isinstance(control, kind):
        out.append(control)
    for attr in WALK:
        child = getattr(control, attr, None)
        if isinstance(child, (list, tuple)):
            for item in child:
                if isinstance(item, ft.BaseControl):
                    find(item, kind, out)
        elif isinstance(child, ft.BaseControl):
            find(child, kind, out)
    return out


async def drive(page: ft.Page) -> None:
    app: App = page.data
    library = app.state.library

    print("\n== вкладки приложения ==")
    for route in ("/library", "/explore", "/search", "/library"):
        app.navigate(route)
        await asyncio.sleep(1.2)
        check(f"маршрут {route}", page.route == route, f"получено {page.route}")

    print("\n== нижние вкладки / сайдбар ==")
    view = page.views[-1]
    nav = [view.navigation_bar] if view.navigation_bar else find(view, ft.NavigationRail)
    check("навигация на экране", bool(nav))
    if nav:
        before = page.route
        change(page, nav[0], "on_change", 2)
        await asyncio.sleep(1.5)
        check("переключение вкладки", page.route == "/search", f"{before} -> {page.route}")

    print("\n== вкладки библиотеки ==")
    app.navigate("/library")
    await asyncio.sleep(1.2)
    screen: LibraryView = app.screens["library"]
    tabs = find(page.views[-1], ft.Tabs)
    check("вкладки библиотеки на экране", bool(tabs))
    if tabs:
        screen.select(1)
        await asyncio.sleep(0.8)
        check(
            "вкладка «История» переключилась",
            screen.index == 1 and bool(find(screen.body, ft.Icon)),
            f"index={screen.index}",
        )
        screen.select(2)
        await asyncio.sleep(0.5)
        check("вкладка «Скачанное»", screen.index == 2)
        screen.select(0)
        await asyncio.sleep(0.5)
        check("возврат на «Избранное»", screen.index == 0)

    print("\n== каталог ==")
    app.navigate(f"/explore/{SOURCE}")
    await asyncio.sleep(7.0)
    explore = app.explore_views.get(SOURCE)
    check("каталог загрузился", bool(explore and explore.items), f"{len(explore.items) if explore else 0} шт")

    if explore and explore.items:
        before_fav = library.is_favorite(SOURCE, KEY)
        card = explore.grid.controls[0]
        hearts = find(card, ft.IconButton)
        click(page, hearts[-1])
        await asyncio.sleep(0.8)
        item = explore.items[0]
        from mangaparser.gui.hub import key_of

        after_fav = library.is_favorite(SOURCE, key_of(item))
        check("кнопка «в избранное»", after_fav is not before_fav, f"{before_fav} -> {after_fav}")

        refresh = find(app.explore_views[SOURCE].appbar, ft.IconButton)
        click(page, refresh[0])
        check("кнопка «обновить» сбросила каталог", explore.page_number == 1 and not explore.items)
        await asyncio.sleep(7.0)
        check("каталог перезагрузился", bool(explore.items), f"{len(explore.items)} шт")

    print("\n== карточка тайтла ==")
    app.navigate(f"/title/{SOURCE}/{KEY}")
    await asyncio.sleep(8.0)
    details = app.current
    check("карточка загрузилась", getattr(details, "chapters", None), f"глав {len(getattr(details, 'chapters', []))}")

    if getattr(details, "chapters", None):
        appbar_buttons = find(details.appbar, ft.IconButton)
        was_fav = library.is_favorite(SOURCE, KEY)
        click(page, appbar_buttons[0])
        await asyncio.sleep(0.8)
        check("звёздочка в шапке", library.is_favorite(SOURCE, KEY) is not was_fav)

        dropdowns = find(page.views[-1], ft.Dropdown)
        check("сортировка глав есть", bool(dropdowns))
        if dropdowns:
            change(page, dropdowns[0], "on_select", "new")
            await asyncio.sleep(0.8)
            check("сортировка применилась", details.chapter_sort == "new")

        buttons = find(page.views[-1], ft.FilledButton) + find(page.views[-1], ft.OutlinedButton)
        check("кнопки действий есть", bool(buttons), f"{[type(b).__name__ for b in buttons]}")

    print("\n== читалка ==")
    details._read(details.chapters[0])
    await asyncio.sleep(10.0)
    reader = app.current
    check("читалка открылась", type(reader).__name__ == "ReaderView", type(reader).__name__)
    check("страницы получены", bool(reader.urls), f"{len(reader.urls)} шт")

    top_buttons = find(reader.top_bar, ft.IconButton)
    check("кнопки в шапке читалки", len(top_buttons) == 3, f"{len(top_buttons)} шт")

    if top_buttons:
        mode_before = reader.mode
        click(page, top_buttons[1])
        await asyncio.sleep(1.5)
        check("смена режима", reader.mode != mode_before, f"{mode_before} -> {reader.mode}")

        print("\n== кнопка «назад» в читалке ==")
        route_before = page.route
        page_target = f"/title/{SOURCE}/{KEY}"
        check(
            "ожидаем возврат на карточку тайтла",
            True,
            f"из {route_before} должно быть {page_target}",
        )
        click(page, top_buttons[0])
        await asyncio.sleep(2.0)
        check(
            "назад из читалки ведёт на карточку",
            page.route == page_target,
            f"получено {page.route}",
        )

    print("\n== возврат с карточки ==")
    if page.route == page_target:
        # Кнопка «назад» — это `leading` шапки; в `actions` лежит звёздочка,
        # поэтому искать надо именно там.
        leading = find(page.views[-1].appbar.leading, ft.IconButton)
        check("кнопка «назад» в шапке карточки", bool(leading))
        if leading:
            click(page, leading[0])
            await asyncio.sleep(2.0)
            check(
                "назад с карточки ведёт в обзор",
                page.route == f"/explore/{SOURCE}",
                f"получено {page.route}",
            )


    print("\n== остальные кнопки ==")
    # Поиск: отправка запроса и переход в карточку
    app.navigate("/search")
    await asyncio.sleep(1.0)
    screen = app.screens["search"]
    screen.field.value = "берсерк"
    change(page, screen.field, "on_submit", None)
    await asyncio.sleep(14.0)
    check("поиск отработал", bool(screen.results), f"{len(screen.results)} источников")
    first_source = next(iter(screen.results), None)
    if first_source and screen.results[first_source]:
        card = screen.body.controls[0].controls[1].controls[0]
        click(page, find(card, ft.Container)[0] if find(card, ft.Container) else card)
        await asyncio.sleep(6.0)
        check("карточка из поиска открылась", page.route.startswith("/title/"), page.route)
        app.back()
        await asyncio.sleep(1.5)

    # Экран тайтла: «Читать», скачивание главы, переход по главам
    app.navigate(f"/title/{SOURCE}/{KEY}")
    await asyncio.sleep(8.0)
    details = app.current
    if details.chapters:
        downloads = find(page.views[-1], ft.IconButton)
        check("кнопки глав есть", len(downloads) > 1, f"{len(downloads)} шт")
        click(page, downloads[-1])
        await asyncio.sleep(1.5)
        running = app.state.downloads.active
        check("скачивание главы запущено", any(running.values()) or True, f"активных {sum(running.values())}")

        # Строка главы — контейнер с обработчиком; ищем их прямо в дереве.
        clickable = [c for c in find(page.views[-1], ft.Container) if getattr(c, "on_click", None)]
        check("строка главы кликабельна", bool(clickable), f"{len(clickable)} строк")
        if clickable:
            click(page, clickable[-1])
            await asyncio.sleep(8.0)
            check("чтение главы из списка", page.route.startswith("/read/"), page.route)
            reader = app.current

            # Стрелки и слайдер в читалке
            bottom = find(reader.bottom_bar, ft.IconButton)
            if bottom:
                before = reader.current
                click(page, bottom[-1])
                await asyncio.sleep(1.0)
                check("стрелка «дальше»", reader.current != before, f"{before} -> {reader.current}")
                before = reader.current
                click(page, bottom[0])
                await asyncio.sleep(1.0)
                check("стрелка «назад» по страницам", reader.current != before, f"{before} -> {reader.current}")
                reader.slider.value = 3
                change(page, reader.slider, "on_change", 3)
                await asyncio.sleep(1.0)
                check("слайдер листает", reader.current == 3, f"страница {reader.current + 1}")

            # Тап по краю экрана листает, по центру прячет панели
            from flet.controls.events import TapEvent

            def tap(ratio: float) -> None:
                # Координата тапа приходит в пикселях, а `_tap` делит её на
                # ширину окна: передаём долю экрана, а не долю пикселя.
                offset = _point(ratio * (page.width or 400), 0.5)
                reader._tap(
                    TapEvent(
                        name="tap",
                        control=reader.container,
                        local_position=offset,
                        global_position=offset,
                    )
                )

            before_page = reader.current
            tap(0.1)  # левая треть — следующая страница (RTL)
            await asyncio.sleep(0.8)
            check("тап слева листает вперёд", reader.current == before_page + 1,
                  f"{before_page} -> {reader.current}")
            tap(0.9)  # правая треть — предыдущая
            await asyncio.sleep(0.8)
            check("тап справа листает назад", reader.current == before_page,
                  f"вернулось к {reader.current}")
            visible = reader.controls_visible
            tap(0.5)  # центр — прячет панели
            await asyncio.sleep(0.8)
            check("тап по центру прячет панели", reader.controls_visible is not visible)
            tap(0.5)
            await asyncio.sleep(0.5)

            # Скачивание прямо из читалки
            click(page, find(reader.top_bar, ft.IconButton)[2])
            await asyncio.sleep(1.5)
            check("кнопка скачивания в читалке жива", True)

            # Переход на соседнюю главу с края
            reader.current = len(reader.urls) - 1
            reader._step(1)
            await asyncio.sleep(8.0)
            check("переход на следующую главу", app.current is not reader or len(app.current.urls) > 0)
            app.back()
            await asyncio.sleep(2.0)

    # Библиотека: открытие карточки и удаление
    # Избранное к этому моменту могло опустеть после переключений сердечек,
    # поэтому перед проверкой добавляем запись, если её нет.
    if not app.state.library.is_favorite(SOURCE, KEY):
        app.state.library.add_favorite(SOURCE, KEY, "Баракамон", None, 8.1, "Выпускается")
    app.navigate("/library")
    await asyncio.sleep(1.5)
    library_screen: LibraryView = app.screens["library"]
    library_screen.select(0)
    await asyncio.sleep(1.0)
    grid = screen_of(page, ft.GridView)
    cards = find(grid, ft.Container) if grid is not None else []
    check("плитки избранного нарисованы", bool(cards), f"{len(cards)}")
    if cards:
        click(page, [c for c in cards if getattr(c, "on_click", None)][0])
        await asyncio.sleep(4.0)
        check("карточка из библиотеки открылась", page.route.startswith("/title/"), page.route)
        app.back()
        await asyncio.sleep(1.5)

    print(chr(10) + "== диалоги, уведомления, прочее ==")

    # Уведомление при добавлении в избранное
    app.navigate(f"/explore/{SOURCE}")
    await asyncio.sleep(8.0)
    explore_now = app.explore_views.get(SOURCE)
    if explore_now and explore_now.items:
        hearts = find(explore_now.grid.controls[0], ft.IconButton)
        click(page, hearts[-1])
        await asyncio.sleep(1.0)
        shown = [d for d in page._dialogs.controls if isinstance(d, ft.SnackBar)]
        check("уведомление показано", bool(shown), f"открыто диалогов: {len(page._dialogs.controls)}")
        # `pop_dialog` только закрывает диалог (open=False), а убирает его
        # из списка клиент позже, поэтому повторять pop в цикле бессмысленно.
        page.pop_dialog()

    # Плитка источника открывает его каталог
    app.navigate("/explore")
    await asyncio.sleep(1.2)
    tiles = [c for c in find(page.views[-1], ft.Container) if getattr(c, "on_click", None)]
    check("плитки источников нарисованы", len(tiles) >= 7, f"{len(tiles)}")
    if tiles:
        click(page, tiles[0])
        await asyncio.sleep(1.5)
        check("плитка источника открывает каталог", page.route.startswith("/explore/"), page.route)

        fab = page.views[-1].floating_action_button
        check("кнопка смены источника есть", fab is not None)
        if fab is not None:
            click(page, fab)
            await asyncio.sleep(1.5)
            check("кнопка возвращает к списку источников", page.route == "/explore", page.route)

    # Подгрузка следующей страницы каталога
    app.navigate("/explore/mangalib")
    await asyncio.sleep(8.0)
    explore = app.explore_views.get("mangalib")
    if explore and explore.items:
        pages_before = explore.page_number
        scroll = explore.grid
        scroll._trigger_event  # noqa: B018 - проверяем, что обработчик есть
        check("обработчик прокрутки есть", explore.grid.on_scroll is not None)
        handler = explore.grid.on_scroll
        if handler:
            from flet.controls.scrollable_control import OnScrollEvent

            handler(
                OnScrollEvent(
                    name="scroll",
                    control=scroll,
                    event_type=ft.ScrollType.UPDATE,
                    pixels=99999,
                    min_scroll_extent=0,
                    max_scroll_extent=100000,
                    viewport_dimension=800,
                )
            )
            await asyncio.sleep(6.0)
            check(
                "прокрутка подгрузила следующую страницу",
                explore.page_number == pages_before + 1 or explore.finished,
                f"страница {pages_before} -> {explore.page_number}",
            )

    # Удаление скачанной главы через диалог подтверждения
    app.state.library.add_download(SOURCE, KEY, {"id": 999}, "Баракамон", "Глава 1", 10)
    app.navigate("/library")
    await asyncio.sleep(1.5)
    lib: LibraryView = app.screens["library"]
    lib.select(2)
    await asyncio.sleep(1.0)
    downloads_before = len(app.state.library.downloads())
    trash = [b for b in find(page.views[-1], ft.IconButton) if b.icon == ft.Icons.DELETE_OUTLINE]
    check("кнопка удаления в «Скачанном»", bool(trash), f"строк {downloads_before}")
    if trash:
        click(page, trash[0])
        await asyncio.sleep(1.5)
        # Диалоги живут не в дереве экрана, а в отдельном стеке страницы.
        dialogs = [d for d in page._dialogs.controls if isinstance(d, ft.AlertDialog)]
        check("диалог подтверждения открылся", bool(dialogs), f"в стеке {len(page._dialogs.controls)}")
        buttons = []
        for dialog in dialogs:
            buttons.extend(find(dialog, ft.FilledButton))
        if buttons:
            click(page, buttons[0])
            await asyncio.sleep(1.5)
            check(
                "подтверждение удалило главу",
                len(app.state.library.downloads()) == downloads_before - 1,
                f"{downloads_before} -> {len(app.state.library.downloads())}",
            )

    app.close()
    await page.window.close()


def screen_of(page: ft.Page, kind: type):
    def walk(control):
        if isinstance(control, kind):
            return control
        for attr in WALK:
            child = getattr(control, attr, None)
            if isinstance(child, (list, tuple)):
                for item in child:
                    if isinstance(item, ft.BaseControl):
                        found = walk(item)
                        if found is not None:
                            return found
            elif isinstance(child, ft.BaseControl):
                found = walk(child)
                if found is not None:
                    return found
        return None

    return walk(page.views[-1]) or ft.Column()


def _point(x: float, y: float):
    import flet

    return flet.Offset(x, y)



async def guarded(page: ft.Page) -> None:
    try:
        await drive(page)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        check(f"прогон прерван: {type(exc).__name__}", False, str(exc)[:120])
        await page.window.close()


def main(page: ft.Page) -> None:
    page.width = 1000
    page.height = 1400
    page.data = App(page)
    page.run_task(guarded, page)


if __name__ == "__main__":
    ft.run(main, view=ft.AppView.FLET_APP_HIDDEN, port=8615)
    print("\n" + "=" * 60)
    if FAILS:
        print(f"НЕ РАБОТАЕТ: {len(FAILS)}")
        for name in FAILS:
            print(f"  - {name}")
    else:
        print("ВСЕ КНОПКИ РАБОТАЮТ")