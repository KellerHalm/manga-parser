# manga-parser

Парсеры каталогов манги: **MangaLIB**, **ReManga**, **InkStory**. Один вход,
одинаковые команды для всех источников, чистая архитектура — новый сайт
подключается без правок логики и CLI.

## Быстрый старт

```bash
pip install -r requirements.txt

python main.py                                    # список источников и команд
python main.py sources                            # только источники
python main.py mangalib search "берсерк"
python main.py remanga  catalog --page 1
python main.py inkstory dump --csv --max-pages 10
python main.py all      search "наруто"           # по всем источникам сразу
```

Формат любой команды: `python main.py <источник> <команда> [аргументы]`.

## Команды

| Команда     | Что делает                                            |
|-------------|-------------------------------------------------------|
| `search`    | серверный поиск по названию (1 запрос, ~0.3 с)         |
| `catalog`   | одна страница каталога (`--page`, `--sort`, `--count`) |
| `latest`    | лента новинок (remanga)                                |
| `details`   | карточка тайтла (slug подставляется автоматически)     |
| `chapters`  | список глав                                            |
| `dump`      | полный обход каталога в JSON + CSV + JSONL             |
| `build`     | собрать JSON/CSV из чекпоинта, без единого запроса     |
| `enrich`    | дозагрузить карточки (inkstory: автор, год, главы)     |
| `index`     | построить офлайн-индекс из выгрузок                   |
| `offline`   | поиск по офлайн-индексу (мгновенно, без сети)         |
| `merge`     | склеить выгрузки разных источников в один файл        |

Примеры:

```bash
python main.py remanga dump --csv --delay 0.3             # весь каталог (~30k)
python main.py remanga dump --max-pages 5                # первые 5 страниц
python main.py remanga details naruto                    # dir подставится сам
python main.py inkstory enrich --limit 200               # обогатить 200 карточек
python main.py index --catalog data/remanga_catalog.json --reset
python main.py offline "берсерк"
python main.py merge data/remanga_catalog.json data/inkstory_catalog.json
```

### Устойчивость к обрывам

`dump` пишет прогресс в `<имя>_catalog.jsonl` по мере обхода. Прерывание
(Ctrl+C, краш, 429) не теряет собранное — повторный запуск продолжит с
чекпоинта. `--fresh` начинает с нуля. При 429 пауза автоматически
удваивается до 60 с.

## Архитектура

Зависимости идут строго сверху вниз, обратных импортов нет.

```
main.py                       единственная точка входа
src/mangaparser/
├── cli/                      argparse, маршрутизация, подсказки
├── application/              сценарии: поиск, обход, enrich, index, merge
├── sources/                  конкретные сайты
│   ├── registry.py           реестр источников (единственное место перечисления)
│   ├── mangalib/             client.py + модель Manga
│   ├── remanga/              client.py + модель Title
│   └── inkstory/             client.py + модель Book + parsing.py (разбор HTML)
├── infrastructure/
│   └── storage/              exporters.py, checkpoint.py, sqlite_index.py
└── core/                     контракты и утилиты, внешних зависимостей нет
    ├── ports.py              протоколы Source / Checkpoint / OfflineIndex
    ├── models.py             Item, Chapter, extract_pages
    ├── http.py               сессия с ретраями, Http, Throttle
    └── text.py               strip_tags, to_float, to_int, label, dedupe
tests/                        80 тестов без обращения к сети
data/                         выгрузки (git-ignored)
```

Правила, которые держат архитектуру чистой:

- **core** не знает ни про один сайт и не импортирует `requests`-логику выше себя.
- **application** работает только через порты `core.ports.Source`; названий
  сайтов в нём нет вообще.
- **sources** могут зависеть от `core`, но не друг от друга и не от
  `application`/`cli`.
- **cli** только собирает аргументы и вызывает сценарии; бизнес-логики в нём нет.

### Как добавить источник

1. Создать `src/mangaparser/sources/<name>/client.py` с классом клиента:

```python
from ...core.http import Http, Throttle, build_session
from ...core.models import Item

class MyClient:
    name = "mysite"
    item_class = MyBook        # dataclass, наследующий Item
    dedupe_key = "id"          # поле дедупликации в выгрузке
    key_hint = "slug"          # что нужно для details()/chapters()

    def catalog(self, page: int = 1, **kw) -> list[MyBook]: ...
    def search(self, query: str, page: int = 1, **kw) -> list[MyBook]: ...
    def details(self, key: str, **kw) -> MyBook: ...
    def chapters(self, key: str, **kw) -> list[dict]: ...
    def iter_catalog(self, *, delay, max_pages=None, checkpoint=None, on_page=None, **kw): ...
```

2. Добавить `build()` и `describe()` в модуль.
3. Вписать одну строку в `sources/registry.py`.

CLI, `application` и тесты при этом не меняются: команды уже есть.

## Выгрузки

`dump` создаёт три файла с одинаковыми данными:

- `<source>_catalog.json` — основной формат;
- `<source>_catalog.csv` — для Excel (UTF-8 BOM, колонки — объединение всех полей);
- `<source>_catalog.jsonl` — чекпоинт по ходу обхода, из него же строится
  офлайн-индекс.

Во всех записях есть колонка `source`, а `title` приведено к общему виду —
поэтому каталоги разных сайтов можно склеивать в один датасет.

## Тесты

```bash
pip install pytest
python -m pytest tests -q     # сеть не используется: ответы API — фикстуры
python -m ruff check .
```

## Зависимости

Python >= 3.11, `requests`, `urllib3`. Больше ничего: парсинг HTML сделан
на регулярных выражениях и JSON-LD, поэтому нет тяжёлых зависимостей.
