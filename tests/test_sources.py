"""Тесты источников на фикстурах — без обращения к сети."""

from __future__ import annotations

import json

from mangaparser.sources.inkstory import parsing
from mangaparser.sources.inkstory.client import InkStoryClient
from mangaparser.sources.mangalib.client import MangaLibClient
from mangaparser.sources.remanga.client import ReMangaClient
from tests.conftest import FakeResponse, FakeSession

MANGALIB_PAGE = {
    "data": [
        {
            "id": 1234,
            "name": "Berserk",
            "rus_name": "Берсерк",
            "eng_name": "Berserk",
            "slug_url": "berserk",
            "is_licensed": True,
            "cover": {"default": "https://img/cover.jpg"},
            "rating": {"average": 9.1, "votes": 5000},
            "status": {"id": 1, "label": "Завершён"},
            "type": {"id": 2, "label": "Манга"},
            "ageRestriction": {"id": 16, "label": "16+"},
            "releaseDateString": "1989",
            "description": "Тёмное фэнтези",
        }
    ]
}

REMANGA_PAGE = {
    "results": [
        {
            "id": 99,
            "dir": "<29.04.2026>naruto_",
            "main_name": "Наруто",
            "secondary_name": "Naruto",
            "avg_rating": 9.2,
            "total_votes": 100,
            "issue_year": 1999,
            "count_chapters": 700,
            "cover": {"high": "/cover/naruto.jpg"},
            "type": {"id": 1, "name": "Манга"},
            "status": {"id": 1, "name": "Завершён"},
            "is_licensed": True,
            "genres": [{"id": 1, "name": "Сёнен"}],
        }
    ],
    "next": "next-page",
}

INKSTORY_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"CollectionPage",
 "mainEntity":{"@type":"ItemList","itemListElement":[
   {"item":{"@type":"ComicSeries","name":"Стальной алхимик",
            "url":"https://inkstory.net/content/steel-alchemist",
            "image":"https://inkstory.net/cover.jpg",
            "description":"<p>Братья Эльрик</p>",
            "genre":["Фэнтези","Приключения"]}}]}}
</script>
</head><body>…</body></html>
"""

INKSTORY_BOOK_HTML = """
<html><head>
<script type="application/ld+json">
{"@type":"ComicSeries","name":"Стальной алхимик",
 "url":"https://inkstory.net/content/steel-alchemist",
 "image":"https://inkstory.net/cover.jpg"}
</script></head>
<body>
<div>Автор : Хиромото Араки</div>
<div>Художник : Хиромото Араки</div>
<div>Год выпуска : 2001</div>
<div>Страна оригинала : Япония</div>
<div>Статус оригинала : Завершён</div>
<div>Главы : 116</div>
<div>Просмотры 1 234 567</div>
<a href="/content/steel-alchemist/chapter/1">Глава 1</a>
<a href="/content/steel-alchemist/chapter/2">Глава 2</a>
</body></html>
"""


class TestMangaLib:
    def client(self, session: FakeSession) -> MangaLibClient:
        return MangaLibClient(session=session, delay=0)

    def test_catalog_maps_api_objects(self) -> None:
        session = FakeSession({"/api/manga": FakeResponse(payload=MANGALIB_PAGE)})
        manga = self.client(session).catalog(page=1)[0]
        assert manga.id == 1234
        assert manga.title == "Берсерк"
        assert manga.status == "Завершён"
        assert manga.type == "Манга"
        assert manga.rating == 9.1
        assert manga.votes == 5000
        assert manga.url == "https://mangalib.me/berserk"
        assert manga.key == "berserk"
        assert manga.flat()["source"] == "mangalib"

    def test_search_passes_query(self) -> None:
        session = FakeSession({"/api/manga": FakeResponse(payload=MANGALIB_PAGE)})
        self.client(session).search("берсерк")
        _, params = session.calls[0]
        assert params["q"] == "берсерк"

    def test_iter_catalog_writes_checkpoint_and_stops_on_empty(self, tmp_path) -> None:
        page = FakeResponse(payload=MANGALIB_PAGE)
        empty = FakeResponse(payload={"data": []})
        session = FakeSession({"/api/manga": page})
        client = self.client(session)

        state = {"calls": 0}

        def responder(url, params):
            state["calls"] += 1
            return empty if state["calls"] > 1 else page

        session.responses["/api/manga"] = responder

        from mangaparser.infrastructure.storage import JsonlCheckpoint

        checkpoint = JsonlCheckpoint(tmp_path / "cp.jsonl")
        items = list(client.iter_catalog(checkpoint=checkpoint, on_page=lambda *_: None))
        checkpoint.close()

        assert len(items) == 1
        rows = [line for line in (tmp_path / "cp.jsonl").read_text(encoding="utf-8").splitlines() if line]
        assert json.loads(rows[0])["id"] == 1234

    def test_api_toast_becomes_error(self) -> None:
        session = FakeSession({"/api/manga": FakeResponse(payload={"data": {"toast": {"message": "капча"}}})})
        client = self.client(session)
        try:
            client.catalog()
        except Exception as exc:
            assert "капча" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("ожидалась ошибка")


class TestReManga:
    def client(self, session: FakeSession) -> ReMangaClient:
        return ReMangaClient(session=session, delay=0)

    def test_catalog_maps_api_objects(self) -> None:
        session = FakeSession({"/search/catalog": FakeResponse(payload=REMANGA_PAGE)})
        title = self.client(session).catalog(page=1)[0]
        assert title.id == 99
        assert title.title == "Наруто"
        assert title.cover == "https://remanga.org/cover/naruto.jpg"
        assert title.genres == ["Сёнен"]
        assert title.type == "Манга"
        assert title.year == 1999

    def test_search_returns_list_and_meta(self) -> None:
        payload = {**REMANGA_PAGE, "meta": {"total_items": 42, "total_pages": 2}}
        session = FakeSession({"/api/v2/search/": FakeResponse(payload=payload)})
        client = self.client(session)
        assert len(client.search("наруто")) == 1
        assert client.search_meta("наруто")["total_items"] == 42

    def test_details_percent_encodes_dir(self) -> None:
        session = FakeSession({"/titles/": FakeResponse(payload=REMANGA_PAGE["results"][0])})
        self.client(session).details("<29.04.2026>naruto_")
        url, _ = session.calls[0]
        assert "%3C29.04.2026%3Enaruto_" in url

    def test_chapter_pages_flattens_nested_content(self) -> None:
        chapter = {"content": [[{"link": "https://img/1.jpg"}, {"link": "https://img/2.jpg"}], {"link": "https://img/3.jpg"}]}
        assert ReMangaClient.chapter_pages(chapter) == [
            "https://img/1.jpg", "https://img/2.jpg", "https://img/3.jpg",
        ]


class TestInkStory:
    def client(self, session: FakeSession) -> InkStoryClient:
        return InkStoryClient(session=session, delay=0)

    def test_catalog_from_jsonld(self) -> None:
        session = FakeSession({"/content": FakeResponse(text=INKSTORY_HTML)})
        books = self.client(session).catalog(page=1)
        assert len(books) == 1
        book = books[0]
        assert book.slug == "steel-alchemist"
        assert book.title == "Стальной алхимик"
        assert book.genres == ["Фэнтези", "Приключения"]
        assert book.description == "Братья Эльрик"

    def test_details_fills_fields_from_page_text(self) -> None:
        session = FakeSession({"/content": FakeResponse(text=INKSTORY_BOOK_HTML)})
        book = self.client(session).details("steel-alchemist")
        assert book.author == "Хиромото Араки"
        assert book.year == 2001
        assert book.source_name == "Япония"
        assert book.status == "Завершён"
        assert book.chapters == 116
        assert book.views == 1234567

    def test_chapters_from_links(self) -> None:
        session = FakeSession({"/content": FakeResponse(text=INKSTORY_BOOK_HTML)})
        chapters = self.client(session).chapters("steel-alchemist")
        assert len(chapters) == 2
        assert chapters[0]["url"] == "https://inkstory.net/content/steel-alchemist/chapter/1"

    def test_parsing_helpers(self) -> None:
        assert parsing.jsonld_nodes("<html>нет разметки</html>") == []
        assert parsing.collection_items("<html></html>") == []
        assert parsing.after_label("Жанры : Фэнтези, Приключения | Глав : 10", "Жанры") == "Фэнтези, Приключения"
        assert parsing.total_pages('<a href="?page=12">', 537) == 12
        assert parsing.total_pages("<a>", 537) == 537

    def test_catalog_page_gt_1_sends_page_param(self) -> None:
        session = FakeSession({"/content": FakeResponse(text=INKSTORY_HTML)})
        self.client(session).catalog(page=3)
        _, params = session.calls[0]
        assert params == {"page": 3}
