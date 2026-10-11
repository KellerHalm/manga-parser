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

        Строковые списки (жанры после склейки в CSV/JSONL) разбираются обратно,
        иначе повторная выгрузка испортила бы тип поля.
        """
        if not dataclasses.is_dataclass(cls):
            raise TypeError(f"{cls.__name__} должен быть dataclass")
        list_fields = {
            f.name for f in dataclasses.fields(cls) if f.type.startswith(("list", "dict", "tuple"))
        }
        kwargs: dict[str, Any] = {}
        for key, value in row.items():
            if key not in {f.name for f in dataclasses.fields(cls)}:
                continue
            if key in list_fields and isinstance(value, str):
                value = [part.strip() for part in value.split(",") if part.strip()]
            kwargs[key] = value
        return cls(**kwargs)


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
