# Команды и база job-hunter

Справочник для Claude и владельца. Как вести поиск по этапам — в `CLAUDE.md`
и skills в `.claude/skills/`.

Вакансии, оценки и воронка лежат в одной базе `me/job-hunter.db` (SQLite).
Команды `hunter` и `vacancies` берут `me/` проекта, откуда бы их ни запустили.
Другая база — глобальный ключ до подкоманды: `vacancies --db путь.db show 123`.

## Коды выхода

- 0 — всё хорошо.
- 1 — частичная неудача (в выводе список).
- 2 — неверные аргументы, нет базы (`vacancies init`) или конфига.
- 3 — блокировка или капча hh.

В базу пишут только `collect`, `fetch`, `mark`, `status`, `rescore`
(кеш оценок), `ui` (этап и заметка из таблицы) и проход `hunter`. `vacancies sql`, `vacancies funnel`,
`vacancies todo` и `vacancies export` базу только читают (открывают её
в режиме «только чтение», схему не обновляют).

## Команды

### `hunter [--days N] [--dry-run] [--no-score]`
Один проход: собрать выдачу по запросам из `me/config.yaml`, скачать тексты
новых вакансий, оценить их по `me/profile.md` и напечатать сводку: прошедшие
порог по группам запросов; под ними — сколько вакансий осталось ниже порога
(список «Ниже порога N» с оценкой и причиной печатается только с `--dry-run`).
`--days` перебивает `search_period` конфига (1–30). `--dry-run` — не помечать
вакансии виденными и ничего не слать. `--no-score` — без текстов и оценки.
В Телеграм шлёт, только если в конфиге `telegram: true`. Нет `me/config.yaml` —
код 2.

### `vacancies init`
Создаёт папку `me/` и базу. Существующее не перезаписывает; у старой базы
обновляет схему.

### `vacancies collect --role R --text T [опции]`
Собирает карточки из выдачи площадки (без полного текста) и помечает их ролью.
Площадка по умолчанию — hh; `--source superjob` идёт на superjob.

```
vacancies collect --role backend --text "python AND (backend OR бэкенд) NOT стажёр" \
  --area 113 --days 30 --pages 5 --work-format remote --experience between3And6
```

- `--text` — язык запросов hh: `AND`, `OR`, `NOT`, кавычки, `NAME:` (поиск по названию).
- `--area` 113 — Россия (по умолчанию), 1 — Москва, 2 — СПб.
- `--days` 1 | 3 | 7 | 30 (по умолчанию 30). `--pages` 1–40 по 50 карточек (по умолчанию 5).
- `--salary N`, `--with-salary`, `--experience` (повторяемый), `--work-format remote|hybrid|on_site|field_work`.
- `--delay` — пауза между запросами, с (по умолчанию 2).
- `--source hh | zarplata | superjob`. У superjob нет `--area`, `--salary`,
  `--experience`, `--with-salary` и форматов кроме `remote`: они
  игнорируются, о чём отчёт пишет строкой. Период он смотрит не дальше
  недели. Узкий запрос (одно слово) площадка уводит на SEO-лендинг — это
  ошибка разбора, уточните формулировку.

Повторный сбор обновляет метаданные и число откликов, полный текст и
вердикты не трогает.

### `vacancies fetch ID|URL … [--role R] [--refresh]`
Скачивает полный текст. Принимает голый id (это hh), `площадка:id`
(`superjob:52295791`) и ссылки hh, zarplata, superjob (любой поддомен). Вакансию, которой нет в базе, добавляет. Уже скачанные
пропускает без `--refresh`. `--role` помечает вакансии ролью с
`query = 'manual'`.

### `vacancies mark ID yes|no|maybe ["комментарий"]`
Вердикт владельца; повторный вызов перезаписывает.

### `vacancies status ID|URL СТАТУС ["заметка"] [--test "что просят"] [--due ГГГГ-ММ-ДД]`
Этап отклика на вакансию. `СТАТУС`: `selected`, `drafted`, `test`, `applied`,
`invited`, `rejected`, `skipped`, `closed`. Вакансия должна быть в базе
(иначе код 2 и подсказка `vacancies fetch`). Повторный вызов меняет этап;
заметка, `--test` и `--due` без нового значения остаются прежними. Каждая
смена этапа попадает в `status_history`; повтор того же статуса (правка
заметки или срока) историю не пополняет. Неверная дата в `--due` — код 2,
база не затрагивается. Этапы ставит владелец или ты с его слов.

### `vacancies funnel`
Таблица откликов по порядку воронки: id, название, компания, статус,
срок тестового, заметка. Вакансии `skipped` и `closed` не показывает —
они в `applications`, видны через `vacancies sql`.

### `vacancies todo`
О чём напомнить: тестовое со сроком в ближайшие 2 дня или просроченное
(пока статус `test`), `drafted` дольше 3 дней, `applied` дольше 7 дней без
смены статуса. Одна строка на напоминание; если напоминать не о чем — ничего
не печатает. Нет `me/profile.md` — печатает строку о первом запуске. Всегда
код 0, базу не создаёт и не меняет: её зовёт хук начала разговора.

### `vacancies show ID …`
Вакансия как Markdown: метаданные, сигналы, роли, вердикт, навыки, описание.

### `vacancies sql "SELECT …" [--json]`
Запрос только на чтение. Таблица с усечением длинных ячеек; `--json` —
полный вывод.

### `vacancies export [--days N] [--out PATH]`
Выгружает вакансии из базы в xlsx — только по просьбе владельца, плановый
проход таблиц не пишет. Берёт найденные за последние `--days` дней (без
флага — все), свежие первыми. По умолчанию кладёт файл в `me/exports/` как
`vacancies-ГГГГ-ММ-ДД_ЧЧММСС.xlsx`; `--out` задаёт другое место. Печатает путь
и число строк. Если выгружать нечего — пишет об этом и файл не создаёт (код 0).

Колонки: ссылка, название, компания, оценка, почему, флаги, роль, статус,
заметка, дата, когда найдена. Статус — этап воронки из `applications`,
у вакансии без отклика — «новая». Таблица — снимок: правки в ней в базу
не возвращаются, воронку ведёт база.

### `vacancies ui [--port N] [--no-open]`
Локальная таблица вакансий в браузере: `http://127.0.0.1:8765`. Показывает
всё, что в базе: ссылку, компанию, оценку, причину, флаги, роль, этап,
заметку и дату находки. Фильтры — период, минимальная оценка, этап, поиск
по названию и компании; сортировка — по дате, оценке или названию.

В отличие от xlsx, правки возвращаются в базу: этап и заметку можно
поставить прямо в строке и сохранить кнопкой. Это тот же `status`, что и
в `vacancies status`: меняется `applications`, история пишется в
`status_history`. Заметку можно сохранить и без этапа: у вакансии может
ещё не быть решения. Вердикты и тексты вакансий таблица не трогает.

`--port` — другой порт, если 8765 занят, `--no-open` — не открывать браузер.
При старте обновляет схему базы, если код новее (как `vacancies init`).
Сервер слушает только `127.0.0.1` (снаружи недоступен), останавливается по
Ctrl+C.

### `vacancies eval --from PATH [--config PATH]`
Проверка оценки модели на вердиктах владельца: берёт из базы `PATH` (в том
числе старой, схемы v1; читает только на чтение, ничего в неё не пишет)
вакансии с вердиктом и полным текстом, оценивает их критериями из секции
`scoring` конфига (`--config`, по умолчанию `me/config.yaml`) и печатает:
сколько всего и сколько не оценено, среднюю оценку по вердиктам, согласие
с порогом и таблицу промахов. Согласие — доля `yes` не ниже порога и `no`
ниже порога среди оценённых; `maybe` в него не входит. Промах — `yes` ниже
порога или `no` не ниже порога. Оценки хранятся только в памяти. Тратит
квоту модели (OpenCode или подписку Claude — смотря что в `scoring.backend`):
вызов на каждые 10 вакансий. Нет секции `scoring` или конфига — код 2;
модель не оценила ничего или в базе нет подходящих вакансий — код 1.

### `vacancies rescore [--days N] [--limit N] [--config PATH]`
Донастройка критериев без сети: после правки `me/profile.md` пересчитывает
оценки вакансий, уже лежащих в базе. Берёт найденные за последние `--days`
дней (по умолчанию 7) с полным текстом; ничего не скачивает — тексты уже
в базе. Новые оценки кладёт в `scores` под новым хешем критериев, так что
повторный запуск с теми же критериями модель не зовёт. `--limit N` берёт только
N самых свежих вакансий — так пилотная пачка не тянет за собой весь корпус;
без флага пересчитываются все. Печатает «Порог N: было X,
стало Y» (сколько вакансий проходило порог до правки и проходит после),
затем списки строк `было → стало · название — почему`: сначала поднявшиеся
выше порога, потом упавшие ниже, потом остальные изменения; «—» вместо
«было» — вакансию раньше не оценивали. «Было» — последняя оценка в базе
до пересчёта, при любых прежних критериях. Тратит квоту модели: вызов на
каждые 10 вакансий без оценки под новыми критериями. Нет базы, секции
`scoring` или конфига — код 2; модель не оценила ничего — код 1 (причина
в выводе). `--limit` меньше 1 — код 2. Если вакансий для пересчёта нет — сообщает об этом, код 0.

## Схема

### `vacancies` — одна строка на вакансию
| Колонка | Смысл |
|---|---|
| `id` | суррогатный номер строки в базе; у вакансий hh совпадает с id вакансии hh |
| `source` | площадка: `hh` (сюда же zarplata — у неё те же id) или `superjob` |
| `external_id` | id вакансии на площадке; вместе с `source` — ключ вакансии |
| `name`, `company`, `company_id` | название, работодатель |
| `url` | ссылка на страницу вакансии на площадке |
| `area` | город |
| `published_at` | дата публикации, ISO 8601; NULL — площадка не отдала (superjob её не отдаёт в выдаче) |
| `salary_from`, `salary_to`, `currency` | вилка; NULL — не указана |
| `gross` | 1 — до вычета налогов, 0 — на руки, NULL — неизвестно |
| `salary_frequency` | частота выплат: `MONTHLY`, `TWICE_PER_MONTH`, `PER_PROJECT`, … |
| `experience` | `noExperience`, `between1And3`, `between3And6`, `moreThan6` |
| `work_formats` | JSON-массив: `REMOTE`, `HYBRID`, `ON_SITE`, `FIELD_WORK` |
| `employment_form` | `FULL`, `PART`, `PROJECT`, … |
| `employer_trusted` | 1 — работодатель проверен hh, 0 — нет |
| `employer_category` | `COMPANY`, `PRIVATE_INDIVIDUAL` (частное лицо), `AGENCY`, … |
| `antifraud_power` | hh пометил вакансию подозрительной: сила пессимизации 0–1; NULL — не помечена или добавлена по ссылке (страница вакансии этого не показывает) |
| `responses_count` | откликов на момент **последнего сбора** — снимок, не живое число |
| `description` | полный текст в Markdown; NULL — не скачан |
| `key_skills` | JSON-массив навыков от работодателя |
| `status` | `active`, `archived`, `deleted` (удалена с hh); NULL — текст не качали |
| `detail_fetched_at` | когда скачан полный текст |
| `card_json` | сырая карточка выдачи; NULL у добавленных по ссылке |
| `detail_json` | сырые данные страницы вакансии |
| `first_seen_at`, `last_seen_at` | первый и последний сбор, ISO 8601 UTC |

### `hits` — по какому запросу и роли вакансия нашлась
`vacancy_id`, `role`, `query` (текст запроса или `manual`), `first_found_at`.
Одна вакансия может найтись под несколькими ролями.

### `verdicts` — оценка владельца
`vacancy_id`, `verdict` (`yes` | `no` | `maybe`), `note`, `updated_at`.

### `seen`, `meta` — состояние планового прохода
`seen`: `key` (`hh:<id>` или `zarplata:<id>`), `first_seen_at`, `sent_at`
(NULL — в Telegram не отправлялась). `meta`: `key`, `value` — служебные
отметки (время последней отправки и т. п.).

### `scores` — оценки модели по критериям
`key` (как в `seen`), `criteria_hash` (хеш критериев, модели и бэкенда — при их
смене оценка считается заново), `score` 0–10, `why`, `flags` (JSON-массив),
`scored_at`. Одна вакансия может иметь несколько оценок — по одной на хеш.

### `latest_scores` — представление: последняя оценка вакансии
`vacancy_id` (число из `scores.key`, соединяется с `vacancies.id`), `score`,
`why`, `flags`, `scored_at`. Берётся самая новая по `scored_at`.

### `applications` — воронка откликов
`vacancy_id`, `status`, `note`, `test_task` (текст тестового), `test_due`
(срок сдачи тестового), `updated_at`. `status`: `selected` (выбрана),
`drafted` (черновик готов), `test` (тестовое), `applied` (откликнулся),
`invited` (приглашение), `rejected` (отказ), `skipped` (пропущена),
`closed` (закрыта) — другие значения база не принимает.

### `status_history` — когда менялся статус отклика
`id`, `vacancy_id`, `status`, `at` — по строке на каждую смену статуса.

## Полезные запросы

Время в базе — UTC в ISO 8601; `date(…, 'localtime')` переводит в местную
дату. Порог берётся из `scoring.threshold` в `me/config.yaml` (в примерах — 5).

Статистика за сегодня — собрано, оценено, прошло порог:
```sql
SELECT
  (SELECT count(*) FROM vacancies
   WHERE date(first_seen_at, 'localtime') = date('now', 'localtime')) AS collected,
  (SELECT count(*) FROM latest_scores
   WHERE date(scored_at, 'localtime') = date('now', 'localtime')) AS scored,
  (SELECT count(*) FROM latest_scores
   WHERE date(scored_at, 'localtime') = date('now', 'localtime') AND score >= 5) AS passed;
```

Всего по ролям — собрано, оценено, прошло порог:
```sql
SELECT h.role, count(DISTINCT v.id) AS collected,
  count(DISTINCT s.vacancy_id) AS scored,
  count(DISTINCT CASE WHEN s.score >= 5 THEN s.vacancy_id END) AS passed
FROM vacancies v JOIN hits h ON h.vacancy_id = v.id
LEFT JOIN latest_scores s ON s.vacancy_id = v.id
GROUP BY h.role ORDER BY collected DESC;
```

Рядом с порогом — оценка 3–4 за последнюю неделю (серый список из дайджеста):
```sql
SELECT v.id, v.name, v.company, s.score, s.why
FROM latest_scores s JOIN vacancies v ON v.id = s.vacancy_id
WHERE s.score BETWEEN 3 AND 4
  AND v.first_seen_at >= date('now', '-7 days')
ORDER BY s.score DESC, v.published_at DESC LIMIT 100;
```

Всего по статусам воронки — текущий этап:
```sql
SELECT status, count(*) AS n FROM applications GROUP BY status ORDER BY n DESC;
```

Отклики без ответа и сколько дней ждём (от смены статуса на `applied`):
```sql
SELECT v.id, v.name, v.company,
  CAST(julianday('now') - julianday(max(h.at)) AS INTEGER) AS days
FROM applications a JOIN vacancies v ON v.id = a.vacancy_id
JOIN status_history h ON h.vacancy_id = a.vacancy_id AND h.status = 'applied'
WHERE a.status = 'applied' GROUP BY v.id ORDER BY days DESC;
```

Лучшие свежие оценки за неделю среди вакансий без отклика:
```sql
SELECT v.id, v.name, v.company, s.score, s.why
FROM latest_scores s JOIN vacancies v ON v.id = s.vacancy_id
LEFT JOIN applications a ON a.vacancy_id = v.id
WHERE a.vacancy_id IS NULL AND v.first_seen_at >= date('now', '-7 days')
ORDER BY s.score DESC LIMIT 20;
```

Воронка в цифрах: сколько вакансий хоть раз побывало на каждом этапе
(по истории, а не по текущему статусу):
```sql
SELECT status, count(DISTINCT vacancy_id) AS n
FROM status_history GROUP BY status
ORDER BY CASE status WHEN 'selected' THEN 1 WHEN 'drafted' THEN 2 WHEN 'test' THEN 3
  WHEN 'applied' THEN 4 WHEN 'invited' THEN 5 WHEN 'rejected' THEN 6
  WHEN 'skipped' THEN 7 ELSE 8 END;
```

Кандидаты без вердикта, с полным текстом:
```sql
SELECT v.id, v.name, v.company, v.salary_from, v.salary_to
FROM vacancies v LEFT JOIN verdicts d ON d.vacancy_id = v.id
WHERE d.vacancy_id IS NULL AND v.description IS NOT NULL;
```

Кому докачать текст в роли (отсев по метаданным):
```sql
SELECT v.id FROM vacancies v JOIN hits h ON h.vacancy_id = v.id
WHERE h.role = 'backend' AND v.description IS NULL
  AND v.antifraud_power IS NULL AND v.employer_category = 'COMPANY'
  AND (v.salary_to IS NULL OR v.salary_to >= 250000)
ORDER BY v.published_at DESC LIMIT 40;
```

Частота навыков по роли:
```sql
SELECT s.value AS skill, count(*) AS n
FROM vacancies v JOIN hits h ON h.vacancy_id = v.id, json_each(v.key_skills) s
WHERE h.role = 'backend'
GROUP BY s.value ORDER BY n DESC LIMIT 30;
```

Вилки по ролям (только рубли):
```sql
SELECT h.role, count(*) AS n, min(v.salary_from), max(v.salary_to), avg(v.salary_to)
FROM vacancies v JOIN hits h ON h.vacancy_id = v.id
WHERE v.currency = 'RUR' GROUP BY h.role;
```

Вакансии на стыке ролей:
```sql
SELECT vacancy_id, group_concat(DISTINCT role) AS roles
FROM hits GROUP BY vacancy_id HAVING count(DISTINCT role) > 1;
```

Поле, которого нет в колонках, — из сырой карточки:
```sql
SELECT id, json_extract(card_json, '$.employmentForm') FROM vacancies LIMIT 5;
```
