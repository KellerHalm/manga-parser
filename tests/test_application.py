"""Тесты прикладного слоя и инфраструктуры хранения."""

from __future__ import annotations

import json
from pathlib import Path

from mangaparser.application import catalog as catalog_uc
from mangaparser.application import enrich as enrich_uc
from mangaparser.application import index as index_uc
from mangaparser.application import merge as merge_uc
from mangaparser.application.browse import format_details, format_row, search
from mangaparser.application.report import CollectingReporter
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
    class EnrichSource(FakeSource):
        item_class = Book
        dedupe_key = "slug"

        def enrich(self, books: list[Book], *, delay: float | None = None) -> list[Book]:
            for book in books:
                book.author = "Автор"
            return books

    class NoEnrichSource(FakeSource):
        pass

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
        assert all(row["author"] == "Автор" for row in JsonlCheckpoint(out).load())

    def test_enrich_respects_limit(self, tmp_path: Path) -> None:
        out = self._checkpoint(tmp_path / "inkstory_catalog.json", count=5)
        written = enrich_uc.enrich_catalog(
            self.EnrichSource([]), out, limit=2, reporter=CollectingReporter(), want_csv=False
        )
        assert written is not None
        assert sum(1 for row in JsonlCheckpoint(out).load() if row.get("author")) == 2

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
