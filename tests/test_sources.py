"""Тесты источников на фикстурах — без обращения к сети."""

from __future__ import annotations

import json

from mangaparser.sources.buzumanga import slug
from mangaparser.sources.buzumanga.client import (
    BuZuMangaClient,
    BuZuMangaError,
    image_url,
    manga_id,
)
from mangaparser.sources.comx import parsing as comx_parsing
from mangaparser.sources.comx.client import ComXClient, ComXError
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


BUZU_ITEM = {
    "id": 23313,
    "title": "Несущий свет",
    "coverImagePath": "/objects/h/uploads/covers/cover.jpg",
    "totalChapters": 30,
    "status": "frozen",
    "translationStatus": "translating",
    "ageRating": "none",
    "isHentai": False,
    "contentType": "manga",
    "workType": "manhwa",
    "alternativeTitles": "Link-boy / 링크보이",
    "tags": ["Сёнэн", "Фэнтези"],
    "averageRating": 8.4,
    "ratingCount": 120,
    "publicationYear": 2026,
    "isBlocked": False,
    "createdAt": "2026-08-10T20:08:30.043Z",
    "updatedAt": "2026-08-10T20:08:47.817Z",
}

BUZU_CARD = {
    **BUZU_ITEM,
    "description": "Кан Гон выжил на необитаемом острове.",
    "bookmarkStats": {"total": 7},
    "originalReleaseDate": "2024-01-15T00:00:00.000Z",
}

BUZU_SEARCH = {"items": [{"id": 2634, "title": "Ненасытный Берсерк", "coverImagePath": "/c.jpg"}]}

BUZU_CHAPTERS = {
    "chapters": [
        {
            "id": 262778,
            "mangaId": 2634,
            "number": 1,
            "title": "Пролог",
            "pageCount": 40,
            "totalLikes": 5,
            "accessible": True,
            "releaseDate": "2026-06-02T05:31:35.214Z",
            "uploadedBy": {"nickname": "Elaige"},
            "guild": {"name": "Happy team"},
        }
    ]
}


COMX_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://com-x.life/news_pages.xml</loc></sitemap>
<sitemap><loc>https://com-x.life/news_pages2.xml</loc></sitemap>
</sitemapindex>
"""

COMX_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://com-x.life/34100-muv-luv.html</loc>
<changefreq>weekly</changefreq><lastmod>2026-10-11T18:38:22+03:00</lastmod></url>
<url><loc>https://com-x.life/1747-strela-sezon-2.5-read.html</loc>
<lastmod>2026-09-01T10:00:00+03:00</lastmod></url>
<url><loc>https://com-x.life/faq.html</loc></url>
</urlset>
"""


class TestComX:
    def client(self, session: FakeSession) -> ComXClient:
        return ComXClient(session=session, delay=0)

    @staticmethod
    def session() -> FakeSession:
        return FakeSession({
            "/sitemap.xml": FakeResponse(text=COMX_INDEX),
            "news_pages2.xml": FakeResponse(text="<urlset></urlset>"),
            "news_pages.xml": FakeResponse(text=COMX_SITEMAP),
        })

    def test_sitemap_index_and_entries_parsed(self) -> None:
        client = self.client(self.session())
        assert client.sitemap_files() == [
            "https://com-x.life/news_pages.xml",
            "https://com-x.life/news_pages2.xml",
        ]
        # не-карточка (faq.html) в каталог не попадает
        assert client.stats() == 2

    def test_catalog_maps_sitemap_rows(self) -> None:
        title = self.client(self.session()).catalog(page=1)[0]
        assert title.id == 34100
        assert title.uid == "34100"
        assert title.key == "34100"
        assert title.title == "Muv Luv"
        assert title.slug == "muv-luv"
        assert title.url == "https://com-x.life/34100-muv-luv.html"
        assert title.updated_at == "2026-10-11T18:38:22+03:00"
        assert title.sitemap == "news_pages.xml"
        assert title.flat()["source"] == "comx"

    def test_slug_with_dots_survives(self) -> None:
        titles = self.client(self.session()).catalog(page=1)
        assert titles[1].id == 1747
        assert titles[1].title == "Strela Sezon 2.5 Read"

    def test_catalog_page_slices_without_requests(self) -> None:
        session = self.session()
        client = self.client(session)
        client.catalog(page=1, count=1)
        calls = len(session.calls)
        assert client.catalog(page=2, count=1)[0].id == 1747
        assert len(session.calls) == calls  # sitemap скачан один раз и закеширован

    def test_sort_oldest_reverses(self) -> None:
        titles = self.client(self.session()).catalog(page=1, sort="oldest")
        assert [t.id for t in titles] == [1747, 34100]

    def test_search_is_local_over_sitemap(self) -> None:
        session = self.session()
        client = self.client(session)
        assert [t.id for t in client.search("muv")] == [34100]
        assert [t.id for t in client.search("Strela Sezon")] == [1747]
        assert client.search("schenok") == []
        # все совпадения считаются локально, sitemap повторно не качается
        assert len(session.calls) == 3

    def test_details_from_sitemap_by_id_and_slug(self) -> None:
        client = self.client(self.session())
        assert client.details("34100").title == "Muv Luv"
        assert client.details("muv-luv").id == 34100

    def test_details_unknown_raises(self) -> None:
        client = self.client(self.session())
        try:
            client.details("999999")
        except ComXError as exc:
            assert "999999" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("ожидалась ошибка")

    def test_chapters_are_unavailable(self) -> None:
        assert self.client(self.session()).chapters("34100") == []

    def test_iter_catalog_writes_checkpoint(self, tmp_path) -> None:
        client = self.client(self.session())

        from mangaparser.infrastructure.storage import JsonlCheckpoint

        checkpoint = JsonlCheckpoint(tmp_path / "cp.jsonl")
        items = list(client.iter_catalog(count=1, checkpoint=checkpoint, on_page=lambda *_: None))
        checkpoint.close()

        assert [t.id for t in items] == [34100, 1747]
        rows = [line for line in (tmp_path / "cp.jsonl").read_text(encoding="utf-8").splitlines() if line]
        assert json.loads(rows[0])["url"] == "https://com-x.life/34100-muv-luv.html"

    def test_parsing_helpers(self) -> None:
        assert comx_parsing.entry("https://com-x.life/1-a.html")["title"] == "A"
        assert comx_parsing.entry("https://com-x.life/faq.html") is None
        assert comx_parsing.entry("https://other.site/1-a.html") is None
        assert comx_parsing.title_from_slug("") == ""
        assert comx_parsing.title_from_slug("vova_petrov") == "Vova Petrov"
        assert comx_parsing.title_from_slug("idm-ON") == "Idm ON"


class TestBuZuManga:
    def client(self, session: FakeSession) -> BuZuMangaClient:
        return BuZuMangaClient(session=session, delay=0)

    def test_catalog_maps_api_objects(self) -> None:
        session = FakeSession({"/api/manga": FakeResponse(payload=[BUZU_ITEM])})
        manga = self.client(session).catalog(page=1)[0]
        assert manga.id == 23313
        assert manga.title == "Несущий свет"
        assert manga.uid == "23313"
        assert manga.key == "23313"
        assert manga.url == "https://buzumanga.com/manga/23313-nesuschij-svet"
        assert manga.genres == ["Сёнэн", "Фэнтези"]
        assert manga.chapters == 30
        assert manga.rating == 8.4
        assert manga.votes == 120
        assert manga.year == 2026
        assert manga.status == "Заморожен"
        assert manga.translation_status == "Переводится"
        assert manga.cover == "/objects/h/uploads/covers/cover.jpg"
        assert manga.flat()["source"] == "buzumanga"

    def test_catalog_page_becomes_offset(self) -> None:
        session = FakeSession({"/api/manga": FakeResponse(payload=[BUZU_ITEM])})
        self.client(session).catalog(page=3, count=100, sort="updated")
        _, params = session.calls[0]
        assert params == {"limit": 100, "offset": 200, "sort": "updated"}

    def test_catalog_first_page_has_no_offset(self) -> None:
        session = FakeSession({"/api/manga": FakeResponse(payload=[BUZU_ITEM])})
        self.client(session).catalog(page=1)
        _, params = session.calls[0]
        assert params == {"limit": 100}

    def test_catalog_joins_tags_list(self) -> None:
        session = FakeSession({"/api/manga": FakeResponse(payload=[])})
        self.client(session).catalog(tags=["Сёнэн", "Фэнтези"], content_type="manga")
        _, params = session.calls[0]
        assert params["tags"] == "Сёнэн,Фэнтези"
        assert params["contentType"] == "manga"

    def test_search_unwraps_items_and_paginates(self) -> None:
        session = FakeSession({"/api/manga/search": FakeResponse(payload=BUZU_SEARCH)})
        client = self.client(session)
        results = client.search("берсерк", page=2)
        assert [m.id for m in results] == [2634]
        _, params = session.calls[0]
        assert params == {"q": "берсерк", "limit": 10, "offset": 10}

    def test_details_fills_description_and_bookmarks(self) -> None:
        session = FakeSession({"/api/manga/": FakeResponse(payload=BUZU_CARD)})
        manga = self.client(session).details("23313")
        assert manga.description == "Кан Гон выжил на необитаемом острове."
        assert manga.bookmarks == 7
        assert manga.original_release == "2024-01-15T00:00:00.000Z"

    def test_details_accepts_url_tail_with_slug(self) -> None:
        session = FakeSession({"/api/manga/": FakeResponse(payload=BUZU_CARD)})
        self.client(session).details("2634-nenasytnyi-berserk")
        url, _ = session.calls[0]
        assert url.endswith("/api/manga/2634")

    def test_details_without_id_in_key_is_error(self) -> None:
        session = FakeSession({"/api/manga/": FakeResponse(payload=BUZU_CARD)})
        try:
            self.client(session).details("berserk")
        except BuZuMangaError as exc:
            assert "id" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("ожидалась ошибка")

    def test_chapters_are_normalized(self) -> None:
        session = FakeSession({"/chapters": FakeResponse(payload=BUZU_CHAPTERS)})
        chapters = self.client(session).chapters("2634")
        assert len(chapters) == 1
        chapter = chapters[0]
        assert chapter["number"] == 1
        assert chapter["id"] == 262778
        assert chapter["title"] == "Пролог"
        assert chapter["page_count"] == 40
        assert chapter["translator"] == "Elaige"
        assert chapter["url"] == "https://buzumanga.com/manga/2634/read/1"

    def test_chapter_pages_addressed_by_number(self) -> None:
        payload = {"pages": ["/objects/h/uploads/pages/1.jpg", "/objects/h/uploads/pages/2.jpg"]}
        session = FakeSession({"/pages": FakeResponse(payload=payload)})
        assert self.client(session).chapter_pages("2634", 1) == payload["pages"]
        assert session.calls[0][0].endswith("/api/manga/2634/chapters/1/pages")

    def test_iter_catalog_writes_checkpoint_and_stops_on_empty(self, tmp_path) -> None:
        page = FakeResponse(payload=[BUZU_ITEM])
        empty = FakeResponse(payload=[])
        state = {"calls": 0}

        def responder(url, params):
            state["calls"] += 1
            return empty if state["calls"] > 1 else page

        session = FakeSession({"/api/manga": responder})
        client = self.client(session)

        from mangaparser.infrastructure.storage import JsonlCheckpoint

        checkpoint = JsonlCheckpoint(tmp_path / "cp.jsonl")
        items = list(client.iter_catalog(checkpoint=checkpoint, on_page=lambda *_: None))
        checkpoint.close()

        assert len(items) == 1
        rows = [line for line in (tmp_path / "cp.jsonl").read_text(encoding="utf-8").splitlines() if line]
        assert json.loads(rows[0])["id"] == 23313

    def test_stats_and_total_pages(self) -> None:
        session = FakeSession({"/stats": FakeResponse(payload={"totalCount": 22685})})
        client = self.client(session)
        assert client.stats() == 22685
        assert client.total_pages() == 227

    def test_enrich_fills_only_card_fields(self) -> None:
        session = FakeSession({"/api/manga/": FakeResponse(payload=BUZU_CARD)})
        # запись из поиска приходит почти без данных — enrich их добирает
        manga = self.client(session).details("23313")
        manga.description = None
        manga.bookmarks = None
        self.client(session).enrich([manga], delay=0)
        assert manga.description == "Кан Гон выжил на необитаемом острове."
        assert manga.bookmarks == 7

    def test_helpers(self) -> None:
        assert manga_id("2634") == "2634"
        assert manga_id("https://buzumanga.com/manga/2634-slug") == "2634"
        assert manga_id("2634-nenasytnyi-berserk") == "2634"
        assert image_url("/objects/a.jpg") == "https://img.buzumanga.org/objects/a.jpg"
        assert image_url("https://cdn/a.jpg") == "https://cdn/a.jpg"
        assert image_url(None) is None

    def test_slugify_matches_site_urls(self) -> None:
        assert slug.slugify("Мастера присматривают за мной") == "mastera-prismatrivayut-za-mnoj"
        assert slug.slugify("Несущий свет") == "nesuschij-svet"
        assert slug.slugify("Berserk of Gluttony") == "berserk-of-gluttony"
        assert slug.slugify("") == ""


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
