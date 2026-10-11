"""Тесты ядра: утилиты, модели, троттлинг."""

from __future__ import annotations

import pytest

from mangaparser.core.http import Throttle
from mangaparser.core.models import Chapter, Item
from mangaparser.core.text import dedupe, label, strip_tags, to_float, to_int
from mangaparser.sources.mangalib.client import Manga
from mangaparser.sources.remanga.client import Title


class TestText:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("<b>Берсерк</b>", "Берсерк"),
            ("&amp; тест", "& тест"),
            ("  много   пробелов ", "много пробелов"),
            ("", ""),
        ],
    )
    def test_strip_tags(self, raw: str, expected: str) -> None:
        assert strip_tags(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [("7,5", 7.5), ("rating: 8.2", 8.2), ("—", None), (None, None), (True, None), (3, 3.0)],
    )
    def test_to_float(self, raw, expected) -> None:
        assert to_float(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [("1 234 просмотра", 1234), ("12", 12), ("нет", None), (None, None), (7.9, 7)],
    )
    def test_to_int(self, raw, expected) -> None:
        assert to_int(raw) == expected

    def test_label_handles_api_objects(self) -> None:
        assert label({"id": 1, "label": "Манга"}) == "Манга"
        assert label({"id": 1, "name": "Manhwa"}) == "Manhwa"
        assert label("Перевод") == "Перевод"
        assert label(None) is None

    def test_dedupe_keeps_first_occurrence(self) -> None:
        rows = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}, {"id": 1, "v": "c"}]
        assert dedupe(rows, "id") == [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]


class TestThrottle:
    def test_rate_limit_doubles_until_cap(self) -> None:
        throttle = Throttle(1.0, max_delay=8.0)
        assert [throttle.on_rate_limit() for _ in range(5)] == [2.0, 4.0, 8.0, 8.0, 8.0]

    def test_success_resets_delay(self) -> None:
        throttle = Throttle(0.5)
        throttle.on_rate_limit()
        throttle.on_rate_limit()
        throttle.on_success()
        assert throttle.current == 0.5


class TestModels:
    def test_item_flat_includes_source_and_title(self) -> None:
        title = Title(id=1, name="Наруто", url="https://remanga.org/naruto", dir="naruto")
        flat = title.flat()
        assert flat["source"] == "remanga"
        assert flat["title"] == "Наруто"
        assert flat["id"] == 1
        assert list(flat)[0] == "source"

    def test_item_flat_joins_lists(self) -> None:
        title = Title(id=1, name="X", url="u", dir="d", genres=["Сёнен", "Боевик"])
        assert title.flat()["genres"] == "Сёнен, Боевик"

    def test_flat_requires_dataclass(self) -> None:
        class Broken(Item):
            title = "t"
            url = "u"

        with pytest.raises(TypeError):
            Broken().flat()

    def test_chapter_flat(self) -> None:
        chapter = Chapter(number=1.0, title="Начало", url="https://x/1", extra={"pages": 20})
        assert chapter.flat() == {
            "id": None, "number": 1.0, "title": "Начало", "url": "https://x/1", "pages": 20,
        }

    def test_manga_defaults(self) -> None:
        manga = Manga(id=7, name="Берсерк", url="https://mangalib.me/berserk", slug="berserk")
        assert manga.uid == "7"
        assert manga.key == "berserk"
        assert manga.title == "Берсерк"

    def test_flat_roundtrip(self) -> None:
        title = Title(id=1, name="Наруто", url="https://remanga.org/x", dir="x", genres=["Сёнен"])
        restored = Title.from_flat(title.flat())
        assert restored == title
        assert restored.flat() == title.flat()
