# Площадка как адаптер: hh и superjob

## Что и почему

Сбор привязан к hh в трёх местах, и ни одно не годится для второй площадки:

| Слой | Что привязано к hh |
|---|---|
| `hunter/sources.py` | `build_url()` собирает URL из hh-параметров (`text`, `area`, `search_period`, `work_format`), `hh_period()` знает дискретные периоды 1/3/7/30 |
| `hunter/corpus/extract.py` | разбор `HH-Lux-InitialState`, `VACANCY_URL = https://hh.ru/vacancy/{id}` |
| `hunter/corpus/store.py` | `vacancies.id INTEGER PRIMARY KEY` без колонки `source`: вакансия superjob с тем же числом склеилась бы с hh |

Плюс `hunter/parse.py` — второй, legacy-парсер выдачи для `run.py`.

zarplata вторым источником не считается: её пул — это пул hh с теми же id, dedupe
схлопывает её карточки. Значит нужна площадка с собственными вакансиями.

Выбрана **superjob.ru**: крупнейший пул из доступных анонимно, лучше всех
закрывает роли «автор/редактор» и «продвижение», карточка структурированная.
Разведка 2026-11-10 (curl, браузерный UA) дала всё нужное — см. «Ограничения».

## Цели

- Одна новая площадка проходит весь путь целиком: выдача → карточка → страница
  вакансии с полным текстом → оценка → дайджест/таблица.
- Знание о конкретном сайте живёт в одном модуле; остальной код спрашивает
  площадку, а не хардкодит домен и формат.
- Старые данные, команды и конфиги продолжают работать без ручной правки:
  вакансии, собранные раньше, остаются вакансиями hh.

## Не-цели

- rabota.ru и career.habr.com в этом шаге не подключаются. Шов должен позволить
  добавить их отдельным модулем, но проверить это сейчас нечем — проверим
  следующей площадкой.
- Не делаем общий «семантический» язык запросов и не переводим hh-синтаксис
  (`NAME:`, `NOT`, `AND`) на язык других площадок.
- Не переписываем парсер hh: он переезжает как есть.
- Не добавляем ключи API, авторизацию и обход капчи.

## Анти-цели

- Копия hh-парсера во втором месте: при смене вёрстки чинить придётся дважды.
- Молчаливый пустой список вместо ошибки, если сайт сменил формат.
- Дубли superjob ↔ hh в дайджесте: одна вакансия, размещённая на обеих
  площадках, не должна занимать две строки.

## Ограничения, найденные разведкой superjob

- Выдача: `https://russia.superjob.ru/vacancy/search/?keywords=…&page=N`,
  40 карточек на страницу, дальше ~14 страниц.
- Период: `period` = 1, 3 или 7 дней. 30 дней площадка не отдаёт.
- Удалёнка: `workFormatTag[0]=81` (офис — 80). Параметр `remoteWork=1` выдача
  игнорирует.
- Узкие запросы (одно слово) 301-редиректят на SEO-лендинг, который добивает
  список похожими вакансиями: `total_found` расходится с числом карточек.
  Двухсловные запросы («редактор удаленно») отдают нормальную выдачу.
- Даты публикации в карточке нет ни в DOM, ни в `window.APP_STATE`
  (есть только `updatedAt`).
- Карточки — `window.APP_STATE` (JSON, ~560 КБ): `vacancy` → `mainInfo`,
  `companyInfo`, `town`, `detailInfo` (`isRemoteWork`), `vacancySalary`
  (`minSalary`, `maxSalary`, `paymentAgreement`, `currency`, `salaryType`).
- Страница вакансии — JSON-LD `JobPosting`: `title`, `datePosted`,
  `description` (HTML полного текста), `hiringOrganization.name`,
  `jobLocation.address.addressLocality`.
- Короткий адрес работает: `/vakansii/x-<id>.html` → 301 на канонический слаг,
  то есть URL можно строить из одного id.
- Внутренние `/0.1/*` и `api.superjob.ru` без ключа недоступны (404 / 401).

## Подходы

1. **Адаптер на площадку + суррогатный id в базе — выбран.** Протокол с пятью
   методами; hh оборачивает существующий код, superjob написан с нуля; в
   `vacancies` появляется `source` + `external_id`, а `id` остаётся числовым
   первичным ключом и становится суррогатным. `hits`, `verdicts`,
   `applications`, `status_history` не меняются вовсе. Цена: числовой id в базе
   для новых строк перестаёт означать «hh id» — команды и выгрузки переводятся
   на поиск по паре `(source, external_id)`.
2. **Составной первичный ключ `(source, external_id)`.** Честнее по смыслу, но
   внешние ключи в четырёх таблицах, вьюха и все запросы `WHERE vacancy_id = ?`
   переписываются, миграция существующей базы — пересоздание таблиц. Много кода
   и риска ради свойства, нужного в основном при отладке.
3. **Функции по площадке со `switch` внутри.** Дифф меньше, но нормализация
   строки, отчёты и обработка ошибок всё равно просят объект — через две
   площадки станет лесенкой `if/elif`.

## Решение

### 1. Модуль `hunter/platforms/`

```
platforms/__init__.py    PLATFORMS, get(name) -> Platform, UnknownPlatform
platforms/base.py        Protocol Platform, dataclass Card, dataclass Detail
platforms/hh.py          перенос sources.py + corpus/extract.py целиком
platforms/superjob.py    новый
```

Протокол:

```python
class Platform(Protocol):
    name: str
    page_size: int          # 50 у hh, 40 у superjob
    max_pages: int          # 40 у hh, 14 у superjob

    def search_url(self, query: SearchQuery, page: int) -> str: ...
    def search_period(self, days: int) -> int: ...       # ближайший поддерживаемый
    def parse_cards(self, html: str) -> list[Card]: ...
    def vacancy_url(self, external_id: str) -> str: ...
    def parse_detail(self, html: str) -> Detail: ...
    def parse_ref(self, ref: str) -> str | None: ...     # id из ссылки, None если не наша
```

`Card` — то, что нужно обоим потребителям сразу: поля для дайджеста
(`Vacancy`) плюс `row` — словарь колонок `vacancies` для корпуса. Так парсер
один, а не два. Свойство `Card.vacancy` собирает `Vacancy`.

`Detail` переезжает из `corpus/extract.py` в `platforms/base.py` и получает
`source` и `external_id: str` вместо `vacancy_id: int`.

hh-модуль — обёртка без изменения логики: `sources.py` и `corpus/extract.py`
переезжают в него, старые файлы удаляются, импорты в `collect.py`, `store.py`,
`fulltext.py`, `run.py` и тестах правятся. `hunter/parse.py` теряет
`parse_search_page` и `_to_vacancy` (их работу делает `Hh.parse_cards`),
`extract_state` остаётся.

`Platform` описывает **сайт**, а не движок: zarplata остаётся отдельной
площадкой с тем же кодом разбора. Поэтому `Hh` параметризован сайтом
(`Hh("hh")`, `Hh("zarplata")` — имя, домен и путь выдачи), а `PLATFORMS` —
`{"hh": Hh("hh"), "zarplata": Hh("zarplata"), "superjob": Superjob()}`.
Так `sources: [hh, zarplata]` продолжает проходить валидацию, `Vacancy.source`
и ключи `seen`/`scores` (`zarplata:<id>`) остаются прежними, и миграция v4 не
трогает ни одной старой строки.

superjob-модуль: `search_url` собирает `keywords` + `period` +
`workFormatTag[0]=81` при `work_format="remote"` + `page`; `parse_cards`
читает `APP_STATE` (основной источник) с запасным разбором DOM;
`vacancy_url` — `/vakansii/x-<id>.html`; `parse_detail` — JSON-LD `JobPosting`.

Что superjob не умеет из `SearchQuery`: `area` (выдача `russia.superjob.ru` —
вся Россия), `salary`, `experience`, `only_with_salary`, форматы кроме
`remote`. Эти поля адаптер игнорирует, а `collect` печатает в отчёте строку
«superjob: area/salary/experience/only_with_salary не поддерживаются —
проигнорированы», когда они заданы. Молча — нельзя: владелец решит, что
фильтр применился.

Если в ответе нет ни `APP_STATE`, ни контейнера карточек
(`f-test-vacancy-item`) — это `Captcha` (блокировка, как у hh), а не
`ParseError`: superjob всегда кладёт состояние в выдачу, значит страница
подменена. Ответ, где контейнер есть, а карточек нет, — законный пустой
результат.

Пагинация: страниц не больше `min(query.max_pages, platform.max_pages)`.
Листание прекращается и тогда, когда страница не дала ни одной новой
вакансии (все id уже учтены) — этим же отсекается SEO-лендинг, который
добивает список похожими карточками.

### 2. Модель

- `Vacancy.published_at` становится `datetime | None`: у superjob даты в карточке
  нет. `sort_by_freshness` ставит `None` в конец, `drop_published_before`
  такие вакансии не выбрасывает (сервер уже отфильтровал по `period`).
  Дайджест печатает для них пустую дату.
- `Vacancy.source` и `Vacancy.vacancy_id` уже есть — модель под площадки готова.
- `Detail.source`, `Detail.external_id` — как выше.

### 3. Миграция базы v4

```sql
ALTER TABLE vacancies ADD COLUMN source TEXT NOT NULL DEFAULT 'hh';
ALTER TABLE vacancies ADD COLUMN external_id TEXT;
UPDATE vacancies SET external_id = CAST(id AS TEXT);
CREATE UNIQUE INDEX vacancies_source_external ON vacancies(source, external_id);
DROP VIEW latest_scores;
CREATE VIEW latest_scores AS
SELECT vacancy_id, score, why, flags, scored_at FROM (
  SELECT v.id AS vacancy_id, s.score, s.why, s.flags, s.scored_at,
         ROW_NUMBER() OVER (PARTITION BY v.id ORDER BY s.scored_at DESC) AS n
  FROM scores s JOIN vacancies v ON s.key = v.source || ':' || v.external_id
) WHERE n = 1;
```

- `id` остаётся `INTEGER PRIMARY KEY` — суррогатным. Старые строки получают
  `source='hh'`, `external_id=id`, поэтому вьюха и все join'ы работают без
  изменений в вызывающем коде.
- `scores.key` и `seen.key` остаются `source:external_id` — их формат уже
  правильный, трогать не нужно.
- `Corpus.upsert_card` / `note_seen` возвращают `(is_new, vacancy_id)`: `add_hit`
  и `fetch_details` нужен суррогатный id. Запись — `ON CONFLICT(source,
  external_id) DO UPDATE … RETURNING id`.
- Новые методы: `Corpus.find(source, external_id) -> int | None`,
  `Corpus.resolve(source, external_id) -> int` (ошибка с подсказкой, если нет).
- `export.py` получает колонку `source` первой.

### 4. Ссылки и CLI

Форма ссылки на вакансию в командах и в выводе — `ref`:

- `139289794` — голое число, это `hh:<число>` (обратная совместимость);
- `superjob:123456789` — пара `source:id` явно;
- ссылка на hh, zarplata или superjob — распознаётся по хосту через
  `Platform.parse_ref`; неизвестный хост даёт прежнюю ошибку
  «Не похоже на вакансию».

`parse_vacancy_ref` переезжает в `platforms/__init__.py` как
`parse_ref(value) -> tuple[str, str]`.

- `show`, `mark`, `status` ищут вакансию по паре и падают с подсказкой, если
  её нет.
- `fetch` наоборот: если вакансии в базе нет — добавляет её через
  `save_detail` (это обещает GUIDE: «Вакансию, которой нет в базе, добавляет»),
  и только потом отдаёт отчёт.
- `show`, `funnel` и `export` печатают `ref`, а не суррогатный `id`: иначе
  владелец скопирует число из таблицы и попадёт в другую вакансию hh. Для
  старых строк `ref` = `hh:<id>`, то есть выглядит как раньше.
- `vacancies collect` получает `--source` (по умолчанию `hh`) и `--pages`
  ограничивается `Platform.max_pages`.
- `run.py`: `_collect` берёт `platform = get(source)` и вызывает
  `search_url` / `parse_cards` / `card.vacancy`; `fulltext.fetch_texts` строит
  адрес через `get(vacancy.source).vacancy_url(vacancy.vacancy_id)`.

### 5. Конфиг

- `sources: [hh, superjob]` — список площадок, по которым идёт проход
  `run.py`. Валидация берёт имена из `PLATFORMS`, а не из константы
  `KNOWN_SOURCES`.
- Язык запросов у площадок разный, поэтому у запроса появляется
  необязательное `text_by_source: {superjob: "редактор удаленно"}`.
  Правило: площадка есть в карте — идёт её текст; `null` в карте — на эту
  площадку запрос не отправляется (в отчёте строка «пропущено»); площадки
  в карте нет — идёт общий `text`. Так старые конфиги валидны, а мусорный
  hh-синтаксис на superjob не улетает: в `defaults` личного конфига стоит
  `text_by_source: {superjob: null}`, и роль, которой нужен superjob,
  переопределяет это своей формулировкой. (Первый вариант правила — «нет
  площадки в карте, значит не отправлять» — ревью забраковало: он молча
  выключал hh у любого запроса с одной лишь superjob-формулировкой.)
- `SearchQuery.text_by_source: Mapping[str, str | None] = {}` (frozen-датакласс,
  поле с `field(default_factory=dict)`). Значение `null` в карте — «на эту
  площадку запрос не отправлять».
- `text_by_source` заполняется только у ролей `author` и `promo`: на superjob
  живой пул контентных и рекламных вакансий. `vibecoder` и остальные роли на
  superjob не идут — в отчёте «пропущено: нет запроса для superjob».
- `search_period`: `Platform.search_period(days)` — hh 1/3/7/30, superjob 1/3/7.
  Просили 30, площадка больше 7 не отдаёт — берём 7 и пишем в отчёте, что
  superjob посмотрен за 7 дней.

### 6. Дайджест и отчёты

- `collect` печатает источник в строке отчёта: «superjob: 40 карточек, новых 12».
- `dedupe` схлопывает по `(company, name)` между группами, то есть между
  источниками. Сравнение получает нормализацию: casefold, убираются кавычки,
  пунктуация, формы «ООО», «ИП», «АО», лишние пробелы. Без неё
  «ООО «Ромашка»» и «Ромашка» — две строки в дайджесте, а это прямой
  анти-цель. Нормализация влияет только на сравнение, в базе остаётся
  исходное название.
- `run.py` при блокировке одной площадки продолжает остальные (сейчас так и
  есть через `blocked_sources`).

### 7. Тесты

- Фикстуры: `superjob_search_editor.html` (выдача с `APP_STATE`),
  `superjob_vacancy_editor.html` (страница с JSON-LD). Размер как у hh-фикстур
  (0,5–1,7 МБ) — репозиторий такие уже держит.
- Регресс hh: `Hh().search_url()` даёт те же URL, что старый `build_url`;
  `Hh().parse_cards()` на существующих фикстурах даёт те же значения, что
  `parse_search_page` (перенос тестов `test_parse.py` на `Card`).
- superjob: URL выдачи (keywords/period/удалёнка/страница), период 1/3/7 и
  обрезка 30 → 7, карточки из `APP_STATE` (зарплата «по договорённости» и
  «от 150 000»), `vacancy_url` по одному id, `parse_detail` из JSON-LD.
- Миграция: база v3 с двумя вакансиями → v4, у обеих `source='hh'`,
  `external_id=id`, `latest_scores` отдаёт оценки.
- Ключи: `hh:1` и `superjob:1` — две разные строки, `upsert_card` не склеивает;
  `resolve` находит обе.
- `parse_ref`: число, `superjob:123`, ссылка hh, ссылка zarplata, ссылка
  superjob (`/vakansii/<слаг>-<id>.html`), мусор.
- `fetch` добавляет вакансию, которой нет в базе, и печатает её `ref`.
- Пагинация: страница без новых id останавливает листание; `max_pages`
  урезается до `platform.max_pages`.
- Дедуп: «ООО «Ромашка»» с hh и «Ромашка» с superjob — одна строка.
- `text_by_source`: запрос без текста для площадки не уходит туда и попадает
  в «пропущено».

## Что остаётся строителю

- Точные селекторы DOM-запасного пути для superjob (основной — `APP_STATE`).
- Формат строки «пропущено» в отчёте `collect`.
- Нужен ли `--source` у `vacancies collect` списком (несколько площадок за
  запуск) или достаточно одного значения — по умолчанию одно.
