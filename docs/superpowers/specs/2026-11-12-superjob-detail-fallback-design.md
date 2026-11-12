# superjob: разбор детали не падает на списках и читает архивные страницы

Дата: 2026-11-12. Площадка: superjob. Файл: `hunter/platforms/superjob.py`.

## Зачем

Проход 11 ноября 2026 (лог `me/logs/hunter-2026-11-11-110928.log`) дал
четыре сбоя разбора страницы вакансии из шести предупреждений:

```
Текст вакансии superjob:52303209 не получен: cannot use 'list' as a dict key (unhashable type: 'list')
Текст вакансии superjob:52284158 не получен: cannot use 'list' as a dict key (unhashable type: 'list')
Текст вакансии superjob:52187159 не получен: cannot use 'list' as a dict key (unhashable type: 'list')
Текст вакансии superjob:52303781 не получен: на странице superjob нет JobPosting — сайт сменил формат
```

Вакансия без текста оценивается только по карточке: теряется опыт, требования,
условия — то, по чему идёт оценка. Все четыре вакансии остались в базе
с пустым `status`, то есть даже не помечены закрытыми.

## Причины (проверены живыми страницами 12.11.2026)

**1. `employmentType` приходит массивом.** Схема schema.org разрешает списком,
superjob этим пользуется:

| id | `employmentType` |
|---|---|
| 52303209 | `["FULL_TIME", "PART_TIME", "TEMPORARY"]` |
| 52284158 | `["FULL_TIME", "PART_TIME"]` |
| 52187159 | `["FULL_TIME", "INTERN"]` |

Код делает `_EMPLOYMENT.get(employment, employment)`. Список как ключ словаря
падает с `TypeError` (в Python 3.14 текст «cannot use 'list' as a dict key»).
`fulltext.py` ловит `TypeError` и пишет предупреждение — проход не рушится,
но текст теряется.

**2. У архивных страниц нет `JobPosting`.** Проверено на девяти страницах:
`JobPosting` есть ровно у опубликованных (`vacancyStatusesInfo.isArchive = false`,
`vacancyPublishedStatusDictionary.key = "st_published"`) и отсутствует у архивных
(`isArchive = true`, `key = "st_archive"`). В JSON-LD у архивной страницы остаётся
только `BreadcrumbList`.

При этом `window.APP_STATE` на странице вакансии полный (`pageInfo.pageType =
"vacancy-page"`, `routeParams.vacancyId`): в нём те же сущности, что разбираются
в карточках выдачи, плюс `vacancyDetailInfo.attributes.fullText` с текстом.

Сообщение «сайт сменил формат» в этом случае неверно и уводит в сторону.

Побочно выяснилось: `parse_detail` всегда пишет `status="active"`, поэтому
закрытая вакансия в базе выглядит живой (`51732192` — пример: в базе `active`,
на сайте архив).

## Что делаем

Вариант А (выбран владельцем): при отсутствии `JobPosting` собирать деталь
из `APP_STATE`, переиспользуя разбор карточек.

1. **Нормализация `employmentType`.** Новая функция `_employment(value)`:
   строка → один элемент; список → первый элемент, который есть в `_EMPLOYMENT`;
   если известных нет — первый элемент как есть (прежнее поведение для
   незнакомого кода). Не строка и не список → `None`. Порядок списка уважаем:
   площадка ставит основную занятость первой.

2. **`parse_detail` читает `APP_STATE` всегда.** Состояние даёт:
   - `pageInfo.routeParams.vacancyId` — id вакансии;
   - `vacancyStatusesInfo` — `archived`, если `isArchive` или `isClosed`, иначе `active`;
   - `vacancyDetailInfo.attributes` — текст для запасного пути.

3. **Запасной путь, если `JobPosting` нет.** Строка вакансии берётся из
   `_card(state, vacancy_id)` — тот же разбор, что для выдачи. Дополнительно:
   - `published_at` ← `vacancyMainInfo.attributes.publishedAt` (в выдаче даты нет);
   - описание ← `vacancyDetailInfo.attributes.fullText`, если пусто —
     `fullTextPlain`, если пусто — `duties` + `requirements`;
   - `key_skills` ← имена из `professionalSkill` по
     `vacancyDetailInfo.relationships.professionalSkills.data`.
   Описание прогоняется через `html_to_markdown`, как и в пути с `JobPosting`.

4. **`_card` переводит `workType` в код hh.** Справочник отдаёт слаги
   (`full-time`, `part-time`), а путь с `JobPosting` — коды (`FULL`, `PART`).
   Новая карта `_WORK_TYPE = {"full-time": "FULL", "part-time": "PART"}`
   выравнивает площадку: сейчас в базе у superjob лежат слаги, а рядом коды hh.
   Незнакомый слаг остаётся как есть.

5. **Ошибки.** Нет `APP_STATE` → `Captcha` (как раньше). Состояние есть, но
   `pageType` не `vacancy-page` или нет `routeParams.vacancyId` → `ParseError`
   с честным текстом. Нет ни `JobPosting`, ни сущностей вакансии в состоянии →
   `ParseError`, а не падение `KeyError`.

## Чего не делаем

- Не трогаем путь с `JobPosting` по части метаданных: он разобран и покрыт
  тестом на живой странице.
- Не добавляем `key_skills` в путь с `JobPosting`: у superjob они сейчас пустые
  везде, менять контракт существующего теста и оценки ради этого в этой задаче
  не будем. Навыки появятся только у страниц, прочитанных из `APP_STATE`.
- Не перекачиваем тексты у уже скачанных вакансий: `has_detail` не меняется.
- Не трогаем сбор карточек и пагинацию.

## Проверка

- Фикстуры: существующая `superjob_vacancy.html` (путь с `JobPosting`) должна
  остаться зелёной без правок.
- Новые тесты на синтетическом `APP_STATE` (без живой страницы, чтобы не тащить
  в публичный репозиторий чужие имена и телефоны):
  - `employmentType` списком даёт `FULL`, одиночная строка — как раньше,
    незнакомый код остаётся собой, `null` → `None`;
  - архивная страница без `JobPosting`: `status == "archived"`, описание из
    `fullText` без тегов, `published_at` из `publishedAt`, `area`, вилка,
    формат работы, навыки;
  - `fullTextPlain`, когда `fullText` пуст;
  - состояние без `routeParams.vacancyId` и состояние без сущностей вакансии —
    `ParseError`.
- Живая проверка после правки: прогнать `parse_detail` по четырём вакансиям
  из лога и убедиться, что текст и статус появились.
