"""Модели данных.

У источников разный состав полей, но общий контракт: `uid`, `title` и
`flat()` — плоская запись для CSV/JSONL. На этом контракте построены
прикладной слой и выгрузки, поэтому новый источник подключается
без правок application/cli.

`Item` — mixin, а не dataclass: он не добавляет колонок, а только
реализует общее поведение поверх полей конкретного источника.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, ClassVar


def _as_list(value: Any) -> list[Any]:
    """Список после выгрузки: строка -> список, None (пустой список) -> []."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return list(value)


class Item:
    """Общий контракт записи каталога любого источника."""

    #: Имя источника — проставляется конкретным подклассом.
    source: ClassVar[str] = "unknown"

    title: str
    url: str

    @property
    def uid(self) -> str:
        """Ключ дедупликации внутри источника."""
        return str(getattr(self, "id", None) or self.url or self.title)

    def flat(self) -> dict[str, Any]:
        """
        Плоская запись для CSV/JSON/JSONL.

        Списки склеиваются в строку, None сохраняется (пустая ячейка в CSV),
        порядок колонок стабилен — dataclasses даёт его по объявлению полей.
        """
        if not dataclasses.is_dataclass(self):
            raise TypeError(f"{type(self).__name__} должен быть dataclass")
        row: dict[str, Any] = {"source": self.source}
        for f in dataclasses.fields(self):
            value = getattr(self, f.name)
            if isinstance(value, (list, tuple, set)):
                value = ", ".join(str(v) for v in value) or None
            row[f.name] = value
        row["title"] = self.title
        return row

    @classmethod
    def from_flat(cls, row: dict[str, Any]) -> Any:
        """
        Обратная операция к `flat()`: запись из выгрузки -> объект источника.

        Списки, склеенные в строку при выгрузке в CSV/JSONL, разбираются
        обратно, иначе повторная выгрузка испортила бы тип поля. Пустой список
        приходит как None — и тоже должен стать списком.
        """
        if not dataclasses.is_dataclass(cls):
            raise TypeError(f"{cls.__name__} должен быть dataclass")
        columns = {f.name: f for f in dataclasses.fields(cls)}
        kwargs: dict[str, Any] = {}
        for key, value in row.items():
            column = columns.get(key)
            if column is None:
                continue
            if column.type.startswith(("list", "tuple")):
                value = _as_list(value)
            kwargs[key] = value
        return cls(**kwargs)


#: Поля, которые обогащение карточкой не перезаписывает: это идентичность
#: записи, а не содержимое карточки.
IDENTITY_FIELDS = frozenset({"source"})


def is_blank(value: Any) -> bool:
    """Пустое значение: None, пустая строка или пустая коллекция."""
    return value is None or (not isinstance(value, (int, float, bool)) and not value)


def merge_details(row: dict[str, Any], details: dict[str, Any]) -> dict[str, Any]:
    """
    Досыпает поля карточки в запись каталога.

    Пустые значения игнорируются: карточка отдаёт не всё, и тогда уже
    собранное из каталога не теряется. Идентичность записи не перезаписывается.
    """
    for key, value in details.items():
        if key in IDENTITY_FIELDS or is_blank(value):
            continue
        row[key] = value
    return row


@dataclass
class Chapter:
    """Глава тайтла в нормализованном виде."""

    number: float | None = None
    title: str | None = None
    url: str | None = None
    id: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def flat(self) -> dict[str, Any]:
        return {"id": self.id, "number": self.number, "title": self.title, "url": self.url, **self.extra}


def extract_pages(chapter: Any) -> list[str]:
    """
    URL страниц главы из ответа API.

    Внутри главы лежит двухуровневый список: каждая «страница» — список
    вариантов картинки ({id, link, height, width}), поэтому структура
    вложенная; некоторые источники отдают сразу объекты.
    """
    pages: list[str] = []
    content = chapter.get("content") if isinstance(chapter, dict) else getattr(chapter, "content", None)
    for group in content or []:
        if isinstance(group, list):
            pages.extend(item["link"] for item in group if isinstance(item, dict) and item.get("link"))
        elif isinstance(group, dict) and group.get("link"):
            pages.append(group["link"])
    return pages
