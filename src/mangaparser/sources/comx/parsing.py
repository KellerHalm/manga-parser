"""Разбор sitemap.xml сайта com-x.life (DLE).

Сайт закрыт антибот-челленджем: любая HTML-страница отдаёт 404 и редиректит
на `/_c?t=...`, где браузер обязан отработать proof-of-work, отпечаток
окружения и детект автоматизации. Обходить это в парсере нельзя.

Единственный источник, который отдаётся открыто, — sitemap, на который сам
сайт ссылается в `robots.txt` (`Sitemap: https://com-x.life/sitemap.xml`).
Поэтому источник честно ограничен тем, что есть в sitemap: URL тайтла, его
числовой id и дата обновления. Название восстанавливается из транслитерированного
slug — это единственное, что сайт публикует.

Разбор вынесен отдельно от HTTP-клиента, чтобы покрыть его тестами на фикстурах.
"""

from __future__ import annotations

import re
from typing import Any

#: Адрес карточки: /<id>-<slug>.html (в slug встречаются точки и запятые).
NEWS_URL_RE = re.compile(r"^https?://com-x\.life/(\d+)-(.+)\.html$", re.I)

_LOC_RE = re.compile(r"<loc>(.*?)</loc>", re.S)
_URL_BLOCK_RE = re.compile(r"<url>(.*?)</url>", re.S)
_LASTMOD_RE = re.compile(r"<lastmod>(.*?)</lastmod>", re.S)


def sitemaps(index: str) -> list[str]:
    """Адреса вложенных sitemap-файлов из `sitemap.xml`."""
    return [loc.strip() for loc in _LOC_RE.findall(index or "") if loc.strip().lower().endswith(".xml")]


def entry(url: str, lastmod: str | None = None, sitemap: str | None = None) -> dict[str, Any] | None:
    """Разбирает один адрес карточки в запись каталога (None, если не карточка)."""
    match = NEWS_URL_RE.match(url.strip())
    if not match:
        return None
    ident, slug = match.groups()
    return {
        "id": int(ident),
        "url": url.strip(),
        "slug": slug,
        "title": title_from_slug(slug),
        "updated_at": lastmod,
        "sitemap": sitemap,
    }


def entries(page: str, sitemap: str | None = None) -> list[dict[str, Any]]:
    """Записи каталога со страницы sitemap (адреса, что не карточки, пропускаются)."""
    out: list[dict[str, Any]] = []
    for block in _URL_BLOCK_RE.findall(page or ""):
        loc = _LOC_RE.search(block)
        if not loc:
            continue
        lastmod = _LASTMOD_RE.search(block)
        row = entry(loc.group(1), lastmod.group(1) if lastmod else None, sitemap)
        if row:
            out.append(row)
    return out


def title_from_slug(slug: str) -> str:
    """
    `schenok-kotorogo-ja-podobral` -> `Schenok Kotorogo Ja Podobral`.

    Обратной транслитерации не существует, поэтому название остаётся
    латиницей — это ровно то, что сайт публикует в sitemap.
    """
    words = [w for w in re.split(r"[-_]+", slug or "") if w]
    return " ".join(w if w.isupper() else w.capitalize() for w in words)