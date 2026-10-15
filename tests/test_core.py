"""Тесты ядра: утилиты, модели, троттлинг."""

from __future__ import annotations

import pytest

from mangaparser.core.http import Http, SourceError, Throttle
from mangaparser.core.models import (
    Chapter,
    Item,
    chapter_field,
    extract_pages,
    is_blank,
    merge_details,
)
from mangaparser.core.text import dedupe, label, strip_tags, to_compact_int, to_float, to_int
from mangaparser.sources.mangalib.client import Manga
from mangaparser.sources.remanga.client import Title
from tests.conftest import FakeResponse, FakeSession


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

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("73M", 73_000_000),
            ("826k", 826_000),
            ("2.91k", 2910),
            ("1.2b", 1_200_000_000),
            ("1 234 567", 1_234_567),
            ("73M просмотров", 73_000_000),
            ("", None),
            (None, None),
            (826, 826),
        ],
    )
    def test_to_compact_int_expands_rounded_counters(self, raw, expected) -> None:
        """Сайты округляют счётчики: «73M» — это 73 миллиона, а не 73."""
        assert to_compact_int(raw) == expected

    def test_label_handles_api_objects(self) -> None:
        assert label({"id": 1, "label": "Манга"}) == "Манга"
        assert label({"id": 1, "name": "Manhwa"}) == "Manhwa"
        assert label("Перевод") == "Перевод"
        assert label(None) is None

    def test_dedupe_keeps_first_occurrence(self) -> None:
        rows = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}, {"id": 1, "v": "c"}]
        assert dedupe(rows, "id") == [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]


class TestHttp:
    @pytest.mark.parametrize("status", [404, 422, 401, 451, 503])
    def test_http_errors_become_source_error(self, status: int) -> None:
        """
        Любой код ответа >= 400 — это SourceError, а не сырое HTTPError.

        Обход каталога и `enrich` ловят SourceError: пропускают запись и идут
        дальше. Страницы, закрытые по решению правообладателя (451), — это
        норма для части каталога, а не сбой обхода.
        """
        session = FakeSession({"/manga/": FakeResponse(status_code=status, url="https://x/manga/1/")})
        with pytest.raises(SourceError, match=None if status in (404, 422) else str(status)):
            Http(session).get_text("https://x/manga/1/")

    def test_successful_response_is_returned_as_is(self) -> None:
        session = FakeSession({"/manga/": FakeResponse(text="карточка")})
        assert Http(session).get_text("https://x/manga/1/") == "карточка"


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

    def test_flat_roundtrip_keeps_empty_lists(self) -> None:
        """Пустой список в выгрузке — это None, и он должен вернуться списком."""
        title = Title(id=1, name="X", url="u", dir="d", genres=["Ужасы"])
        restored = Title.from_flat(title.flat())
        assert restored.genres == ["Ужасы"]
        assert restored.categories == [] and restored.publishers == []

    def test_merge_details_fills_and_keeps_existing(self) -> None:
        row = {"slug": "x", "title": "X", "rating": 7.5, "description": None}
        merge_details(row, {"description": "Описание", "rating": None, "authors": ["Ода"]})
        assert row["description"] == "Описание"
        assert row["authors"] == ["Ода"]
        # пустое значение карточки не затирает уже собранное
        assert row["rating"] == 7.5

    def test_merge_details_never_touches_identity(self) -> None:
        """Происхождение записи задаётся выгрузкой, карточка его не меняет."""
        row = {"slug": "x", "source": "inkstory"}
        merge_details(row, {"source": "чужой", "title": "Обновлённое"})
        assert row["source"] == "inkstory"
        assert row["title"] == "Обновлённое"

    def test_is_blank_treats_zero_as_value(self) -> None:
        assert is_blank(None) and is_blank("") and is_blank([]) and is_blank({})
        assert not is_blank(0) and not is_blank(False) and not is_blank("x")

    def test_chapter_flat(self) -> None:
        chapter = Chapter(number=1.0, title="Начало", url="https://x/1", extra={"pages": 20})
        assert chapter.flat() == {
            "id": None, "number": 1.0, "title": "Начало", "url": "https://x/1", "pages": 20,
        }

    def test_chapter_field_reads_dict_and_object(self) -> None:
        assert chapter_field({"number": 3}, "number") == 3
        assert chapter_field(Chapter(number=4.0), "number") == 4.0
        # значение None — это значение, а не отсутствие ключа
        assert chapter_field({"title": None}, "title", "-") is None
        assert chapter_field({}, "title", "-") == "-"
        assert chapter_field({}, "url") is None

    def test_extract_pages_understands_every_source_shape(self) -> None:
        # ReManga: двухуровневый content, каждая страница — список вариантов
        assert extract_pages({"content": [
            [{"link": "https://img/1.jpg"}, {"link": "https://img/1-big.jpg"}],
            [{"link": "https://img/2.jpg"}],
        ]}) == ["https://img/1.jpg", "https://img/1-big.jpg", "https://img/2.jpg"]
        # плоский content без вложенности
        assert extract_pages({"content": [{"link": "https://img/1.jpg"}]}) == ["https://img/1.jpg"]
        # pages: строки и объекты с разными именами поля
        assert extract_pages({"pages": [
            "https://img/1.jpg",
            {"url": "https://img/2.jpg"},
            {"image": "https://img/3.jpg"},
            {"src": "https://img/4.jpg"},
            {"broken": "https://img/5.jpg"},
            "",
        ]}) == [
            "https://img/1.jpg",
            "https://img/2.jpg",
            "https://img/3.jpg",
            "https://img/4.jpg",
        ]

    def test_extract_pages_of_empty_chapter(self) -> None:
        assert extract_pages({}) == []
        assert extract_pages({"content": []}) == []
        # число страниц вместо списка адресов — не список и не страницы
        assert extract_pages({"pages": 12}) == []
        assert extract_pages(None) == []

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
