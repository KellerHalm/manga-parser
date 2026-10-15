"""Тесты прикладного слоя и инфраструктуры хранения."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mangaparser.application import catalog as catalog_uc
from mangaparser.application import enrich as enrich_uc
from mangaparser.application import index as index_uc
from mangaparser.application import merge as merge_uc
from mangaparser.application.browse import (
    chapter_pages,
    chapters,
    format_chapter,
    format_details,
    format_row,
    search,
)
from mangaparser.application.report import CollectingReporter
from mangaparser.core.http import SourceError
from mangaparser.core.ports import Source
from mangaparser.infrastructure.storage import JsonlCheckpoint, SqliteIndex, finalize
from mangaparser.sources.inkstory.client import Book
from mangaparser.sources.mangalib.client import Manga


class FakeSource:
    """Источник без сети: отдаёт заранее заданные страницы."""

    name = "fake"
    dedupe_key = "id"

    def __init__(self, pages: list[list[Manga]], catalog_signature: str = "fake") -> None:
        self.pages = pages
        self.signature = catalog_signature

    def catalog(self, page: int = 1, **_: object) -> list[Manga]:
        return self.pages[page - 1] if page <= len(self.pages) else []

    def search(self, query: str, page: int = 1, **_: object) -> list[Manga]:
        return [m for m in self.pages[0] if query.lower() in m.title.lower()]

    def details(self, key: str, **_: object) -> Manga:
        return next(m for m in self.pages[0] if m.key == key)

    def chapters(self, key: str, **_: object) -> list[dict]:
        return [{"id": 1, "number": 1.0, "title": "start", "url": f"https://x/{key}/1"}]

    def chapter_pages(self, key: str, chapter: dict, **_: object) -> list[str]:
        return [f"https://img/{chapter['id']}-{index}.jpg" for index in range(3)]

    def iter_catalog(self, *, checkpoint=None, on_page=None, **_: object):
        seen: set[int] = set()
        for page, items in enumerate(self.pages, 1):
            fresh = [m for m in items if m.id not in seen]
            seen.update(m.id for m in fresh)
            if not fresh:
                if on_page:
                    on_page("duplicates", page)
                return
            if checkpoint is not None:
                checkpoint.append([m.flat() for m in fresh])
                checkpoint.flush()
            if on_page:
                on_page("page", page, len(fresh), len(seen))
            yield from fresh


def manga(uid: int, title: str) -> Manga:
    return Manga(id=uid, name=title, url=f"https://mangalib.me/{uid}", slug=str(uid))


def test_source_protocol():
    assert isinstance(FakeSource([]), Source)


class TestCheckpoint:
    def test_append_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "cp.jsonl"
        with JsonlCheckpoint(path) as cp:
            cp.append([{"id": 1}, {"id": 2}])
        assert JsonlCheckpoint(path).load() == [{"id": 1}, {"id": 2}]

    def test_load_skips_broken_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "cp.jsonl"
        path.write_text('{"id": 1}\nмусор\n\n{"id": 2}\n', encoding="utf-8")
        assert JsonlCheckpoint(path).load() == [{"id": 1}, {"id": 2}]

    def test_load_missing_file(self, tmp_path: Path) -> None:
        assert JsonlCheckpoint(tmp_path / "нет.jsonl").load() == []


class TestFinalize:
    def test_dedupes_and_writes_all_formats(self, tmp_path: Path) -> None:
        out = tmp_path / "fake_catalog.json"
        written = finalize(
            [{"id": 1, "title": "A"}, {"id": 1, "title": "A2"}, {"id": 2, "title": "B"}],
            out, key="id", want_csv=True,
        )
        assert written["rows"] == 2
        assert json.loads(out.read_text(encoding="utf-8"))[0]["title"] == "A"
        assert written["csv"].read_text(encoding="utf-8-sig").count("\n") == 3
        assert len(written["jsonl"].read_text(encoding="utf-8").strip().splitlines()) == 2

    def test_columns_are_union_of_all_keys(self, tmp_path: Path) -> None:
        out = tmp_path / "c.json"
        written = finalize([{"id": 1, "a": "x"}, {"id": 2, "b": "y"}], out, key="id")
        assert written["csv"].read_text(encoding="utf-8-sig").splitlines()[0] == "id,a,b"

    def test_rows_without_key_are_not_collapsed(self, tmp_path: Path) -> None:
        """Каталог без поля-ключа (только slug) не должен схлопнуться в одну запись."""
        out = tmp_path / "c.json"
        rows = [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}]
        assert finalize(rows, out, key="id", want_csv=False)["rows"] == 3


class TestDumpCatalog:
    def test_full_run_writes_files(self, tmp_path: Path) -> None:
        source = FakeSource([[manga(1, "A"), manga(2, "B")], [manga(3, "C")]])
        out = tmp_path / "fake_catalog.json"
        reporter = CollectingReporter()
        written = catalog_uc.dump_catalog(source, out, delay=0, reporter=reporter, want_csv=True)

        assert written["rows"] == 3
        assert reporter.pages == 2
        assert out.exists() and written["csv"].exists()

    def test_resume_uses_checkpoint(self, tmp_path: Path) -> None:
        out = tmp_path / "fake_catalog.json"
        JsonlCheckpoint(out.with_suffix(".jsonl")).append([{"id": 9, "title": "OLD", "source": "fake"}])
        source = FakeSource([[manga(1, "A")]])
        written = catalog_uc.dump_catalog(
            source, out, delay=0, reporter=CollectingReporter(), want_csv=False
        )
        assert written["rows"] == 2

    def test_fresh_discards_checkpoint(self, tmp_path: Path) -> None:
        out = tmp_path / "fake_catalog.json"
        JsonlCheckpoint(out.with_suffix(".jsonl")).append([{"id": 9, "title": "OLD"}])
        source = FakeSource([[manga(1, "A")]])
        written = catalog_uc.dump_catalog(
            source, out, delay=0, reporter=CollectingReporter(), want_csv=False, resume=False
        )
        assert written["rows"] == 1

    def test_build_from_checkpoint_without_network(self, tmp_path: Path) -> None:
        out = tmp_path / "fake_catalog.json"
        JsonlCheckpoint(out.with_suffix(".jsonl")).append([{"id": 1, "title": "A"}, {"id": 1, "title": "A"}])
        written = catalog_uc.build_from_checkpoint(
            FakeSource([]), out, reporter=CollectingReporter(), want_csv=False
        )
        assert written["rows"] == 1


class TestBrowseFormatting:
    def test_search_uses_source(self) -> None:
        source = FakeSource([[manga(1, "Берсерк"), manga(2, "Наруто")]])
        found = search(source, "бер", reporter=CollectingReporter())
        assert [m.id for m in found] == [1]

    def test_format_helpers(self) -> None:
        item = manga(5, "Берсерк")
        assert "Берсерк" in format_row(item)
        assert "url" in format_details(item)
        assert "url" in format_details({"url": "u", "title": "t"})

    def test_chapters_show_page_count_when_source_knows_it(self) -> None:
        """Колонка страниц заполняется тем, что глава уже знает о себе."""
        report = CollectingReporter()
        items = chapters(
            FakeSource([[manga(1, "x")]]),
            "berserk",
            with_pages=True,
            reporter=report,
        )
        assert items[0]["pages_count"] == 3
        assert "стр.3" in format_chapter(items[0])

    def test_chapters_without_with_pages_do_not_call_the_source(self) -> None:
        source = FakeSource([[manga(1, "x")]])
        items = chapters(source, "berserk", reporter=CollectingReporter())
        assert "pages_count" not in items[0]

    def test_chapter_pages_takes_the_first_chapter_by_default(self) -> None:
        source = FakeSource([[manga(1, "x")]])
        pages = chapter_pages(source, "berserk", reporter=CollectingReporter())
        assert pages == ["https://img/1-0.jpg", "https://img/1-1.jpg", "https://img/1-2.jpg"]

    def test_chapter_pages_rejects_index_out_of_range(self) -> None:
        source = FakeSource([[manga(1, "x")]])
        with pytest.raises(SourceError, match="Главы с номером 5 нет"):
            chapter_pages(source, "berserk", index=5, reporter=CollectingReporter())

    def test_chapter_pages_fails_when_title_has_no_chapters(self) -> None:
        class NoChapters(FakeSource):
            def chapters(self, key: str, **_: object) -> list[dict]:
                return []

        with pytest.raises(SourceError, match="нет глав"):
            chapter_pages(NoChapters([]), "x", reporter=CollectingReporter())

    def test_chapter_pages_limit_caps_output_only(self, capsys) -> None:
        """`limit` ограничивает печать, а не результат — как у `chapters`."""
        source = FakeSource([[manga(1, "x")]])
        pages = chapter_pages(source, "x", reporter=CollectingReporter(), limit=1)
        assert len(pages) == 3
        printed = [line for line in capsys.readouterr().out.splitlines() if "https://img" in line]
        assert len(printed) == 1


class TestIndex:
    def test_build_and_search_offline(self, tmp_path: Path) -> None:
        catalog = tmp_path / "mangalib_catalog.json"
        catalog.write_text(
            json.dumps(
                [
                    {"id": 1, "title": "Стальной алхимик", "source": "mangalib"},
                    {"id": 2, "title": "Наруто", "source": "mangalib"},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        index_path = index_uc.build_index([catalog], tmp_path / "index.db", reporter=CollectingReporter())
        hits = index_uc.search_index(index_path, "сталь", reporter=CollectingReporter())
        assert [h["title"] for h in hits] == ["Стальной алхимик"]

    def test_prefix_and_multiple_tokens(self, tmp_path: Path) -> None:
        with SqliteIndex(tmp_path / "i.db") as index:
            index.bulk({1: ("mangalib", "Solo Leveling"), 2: ("mangalib", "Берсерк")})
            assert [h["id"] for h in index.search("sol lev")] == [1]
            assert [h["id"] for h in index.search("бер")] == [2]
            assert index.search("нет такого") == []
            assert index.search("") == []

    def test_stable_uid_is_deterministic(self) -> None:
        row = {"slug": "abc", "title": "X"}
        assert index_uc.stable_uid(row) == index_uc.stable_uid(dict(row))
        assert index_uc.stable_uid({"id": 5}) == 5

    def test_bulk_replaces_existing_document(self, tmp_path: Path) -> None:
        with SqliteIndex(tmp_path / "i.db") as index:
            index.bulk({1: ("mangalib", "Старое")})
            index.bulk({1: ("mangalib", "Новое")})
            assert index.count() == 1
            assert index.search("новое")[0]["id"] == 1


class TestEnrich:
    """Обогащение универсально: источнику достаточно уметь details()."""

    class EnrichSource(FakeSource):
        key_hint = "slug"

        def details(self, key: str, **_: object) -> Book:
            return Book(
                slug=key,
                url=f"https://inkstory.net/content/{key}",
                title=f"Т {key}",
                author="Автор",
                publishers=["Азбука"],
                genres=["Фэнтези"],
            )

    class FlakySource(FakeSource):
        key_hint = "slug"

        def details(self, key: str, **_: object) -> Book:
            if key == "slug-1":
                raise SourceError("404")
            return Book(slug=key, url=f"https://inkstory.net/content/{key}", title=key)

    class DirSource(FakeSource):
        """Адресуется полем dir, а не slug — как ReManga."""

        key_hint = "dir"

        def details(self, key: str, **_: object) -> Book:
            return Book(slug="ignored", url="https://x", title=key, author=f"автор {key}")

    class NoEnrichSource(FakeSource):
        enrichable = False

    @staticmethod
    def _checkpoint(path: Path, count: int = 2) -> Path:
        JsonlCheckpoint(path.with_suffix(".jsonl")).append(
            [
                {"slug": f"slug-{i}", "title": f"Т{i}", "url": f"https://inkstory.net/content/slug-{i}"}
                for i in range(count)
            ]
        )
        return path.with_suffix(".jsonl")

    def test_enrich_updates_checkpoint_and_outputs(self, tmp_path: Path) -> None:
        out = self._checkpoint(tmp_path / "inkstory_catalog.json")
        written = enrich_uc.enrich_catalog(
            self.EnrichSource([]), out, limit=10, reporter=CollectingReporter(), want_csv=False
        )
        assert written is not None and written["rows"] == 2
        rows = JsonlCheckpoint(out).load()
        assert all(row["author"] == "Автор" for row in rows)
        assert all(row["genres"] == "Фэнтези" for row in rows)
        assert all(row[enrich_uc.DETAILS_MARK] for row in rows)

    def test_enrich_respects_limit(self, tmp_path: Path) -> None:
        out = self._checkpoint(tmp_path / "inkstory_catalog.json", count=5)
        written = enrich_uc.enrich_catalog(
            self.EnrichSource([]), out, limit=2, reporter=CollectingReporter(), want_csv=False
        )
        assert written is not None
        assert sum(1 for row in JsonlCheckpoint(out).load() if row.get("author")) == 2

    def test_limit_zero_enriches_everything(self, tmp_path: Path) -> None:
        out = self._checkpoint(tmp_path / "inkstory_catalog.json", count=4)
        enrich_uc.enrich_catalog(
            self.EnrichSource([]), out, limit=0, reporter=CollectingReporter(), want_csv=False
        )
        assert sum(1 for row in JsonlCheckpoint(out).load() if row.get("author")) == 4

    def test_second_run_skips_enriched_rows(self, tmp_path: Path) -> None:
        """Обогащённое помечается, поэтому повторный запуск не ходит на сайт заново."""
        out = self._checkpoint(tmp_path / "inkstory_catalog.json")
        source = self.EnrichSource([])
        enrich_uc.enrich_catalog(source, out, limit=10, reporter=CollectingReporter(), want_csv=False)

        class Counting(self.EnrichSource):
            calls = 0

            def details(self, key: str, **_: object) -> Book:
                type(self).calls += 1
                return Book(slug=key, url="https://x", title=key)

        enrich_uc.enrich_catalog(
            Counting([]), out, limit=10, reporter=CollectingReporter(), want_csv=False
        )
        assert Counting.calls == 0

    def test_refresh_reloads_marked_rows(self, tmp_path: Path) -> None:
        out = self._checkpoint(tmp_path / "inkstory_catalog.json")
        source = self.EnrichSource([])
        enrich_uc.enrich_catalog(source, out, limit=10, reporter=CollectingReporter(), want_csv=False)
        source2 = self.EnrichSource([])
        enrich_uc.enrich_catalog(
            source2, out, limit=10, refresh=True, reporter=CollectingReporter(), want_csv=False
        )
        assert all(row["author"] == "Автор" for row in JsonlCheckpoint(out).load())

    def test_addressing_uses_source_key_hint(self, tmp_path: Path) -> None:
        """Запись адресуется полем, которое источник назвал в key_hint."""
        out = tmp_path / "remanga_catalog.json"
        JsonlCheckpoint(out.with_suffix(".jsonl")).append(
            [{"id": 7, "dir": "<29.04.2026>naruto_", "name": "Наруто", "url": "https://remanga.org/x"}]
        )
        enrich_uc.enrich_catalog(
            self.DirSource([]), out, limit=1, reporter=CollectingReporter(), want_csv=False
        )
        row = JsonlCheckpoint(out.with_suffix(".jsonl")).load()[0]
        assert row["author"] == "автор <29.04.2026>naruto_"

    def test_failed_card_does_not_stop_the_run(self, tmp_path: Path) -> None:
        out = self._checkpoint(tmp_path / "inkstory_catalog.json", count=3)
        written = enrich_uc.enrich_catalog(
            self.FlakySource([]), out, limit=10, reporter=CollectingReporter(), want_csv=False
        )
        assert written is not None and written["rows"] == 3
        rows = {row["slug"]: row for row in JsonlCheckpoint(out).load()}
        assert enrich_uc.DETAILS_MARK not in rows["slug-1"]
        assert all(enrich_uc.DETAILS_MARK in rows[f"slug-{i}"] for i in (0, 2))

    def test_source_without_enrich(self, tmp_path: Path) -> None:
        assert enrich_uc.enrich_catalog(
            self.NoEnrichSource([]), tmp_path / "x.json", reporter=CollectingReporter()
        ) is None

    def test_missing_checkpoint(self, tmp_path: Path) -> None:
        assert enrich_uc.enrich_catalog(
            self.EnrichSource([]), tmp_path / "нет.json", reporter=CollectingReporter()
        ) is None


class TestMerge:
    def test_merge_prefixes_uid(self, tmp_path: Path) -> None:
        a = tmp_path / "mangalib_catalog.json"
        b = tmp_path / "remanga_catalog.json"
        a.write_text(json.dumps([{"id": 1, "title": "A"}]), encoding="utf-8")
        b.write_text(json.dumps([{"id": 1, "title": "B"}]), encoding="utf-8")
        out = tmp_path / "all_catalog.json"
        written = merge_uc.merge_catalogs([a, b], out, want_csv=False, reporter=CollectingReporter())
        assert written["rows"] == 2
        rows = json.loads(out.read_text(encoding="utf-8"))
        assert {r["uid"] for r in rows} == {"mangalib:1", "remanga:1"}
