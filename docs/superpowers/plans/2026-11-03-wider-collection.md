# Шире сбор и второй список в дайджесте — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Собрать в разы больше вакансий по четырём ролям и показывать в дайджесте серую зону (3–4 балла) и вакансии без оценки, не смешивая их с прошедшими порог.

**Architecture:** Роль становится полем запроса (`SearchQuery.role`), дайджест группирует по роли, а корпус по-прежнему пишет конкретный запрос в `hits`. `run_pass` раскладывает оценённые вакансии на четыре корзины — прошли порог, «рядом», без оценки, ниже — и передаёт их в `render`/`render_console`.

**Tech Stack:** Python 3.11+, pytest, PyYAML, SQLite (`me/job-hunter.db`), CLI `.venv/bin/hunter` и `.venv/bin/vacancies`.

**Spec:** `docs/superpowers/specs/2026-11-03-wider-collection-design.md`

## Global Constraints

- Порог дайджеста — `threshold: 5`; «рядом» — от `nearby_threshold: 3` до порога.
- Телеграм: «рядом» — не больше 40 строк, «без оценки» — не больше 20; в консоли оба списка целиком.
- Запросы: `search_period: 7`, `area: 113`, `work_format: remote`, `only_with_salary: false`.
- `max_pages` по ролям: author — 4, vibecoder — 3, content-ai — 3, promo — 6.
- Деньги: 60 тыс./мес — минимум только для полной занятости; проект и частичная считаются по объёму.
- Роли в дайджесте: `author`, `vibecoder`, `content-ai`, `promo`.
- Код и тексты — по-русски, как в проекте; сообщения об ошибках конфига называют поле и допустимые значения.
- Тесты гоняются через `.venv/bin/pytest`.

## Review Focus

- Один и тот же запрос-дубль нашёлся двумя запросами одной роли: в дайджесте один блок, в `hits` — роль, а не имя запроса.
- Оценка по карточке (`from_card`) ниже порога: вакансия идёт в «рядом», а не в основной список, и звёздочка остаётся.
- Сбой модели: сотни вакансий без оценки не должны раздувать дайджест — блок «Без оценки» с потолком.
- Граница потолка: ровно 40/20 — без строки «ещё N»; 41/21 — со строкой.
- Запрос с пустым или нестроковым `role`: конфиг падает с понятным текстом, а не молча подставляет имя.

---

### Task 1: Роль как поле запроса

**Files:**
- Modify: `hunter/models.py:10-22`
- Modify: `hunter/config.py:13-23`, `hunter/config.py:84-119`
- Modify: `hunter/run.py:191-197`, `hunter/run.py:265-279`
- Test: `tests/test_config.py`, `tests/test_run.py`

**Interfaces:**
- Consumes: `Config.queries: tuple[SearchQuery, ...]`.
- Produces:
  - `SearchQuery.role: str` — всегда заполнено; без явного `role` равно `name`.
  - `_merge_by_role(groups: list[Group], queries: tuple[SearchQuery, ...]) -> list[Group]` в `hunter/run.py` — склеивает группы запросов одной роли в порядке первого появления роли.
  - `_note_in_corpus(store: Store, fresh: list[Group], queries: dict[str, SearchQuery]) -> None`.

- [ ] **Step 1: Написать падающие тесты конфига**

```python
def test_role_defaults_to_query_name(tmp_path):
    path = _write(tmp_path, """
sources: [hh]
queries:
  - name: "Автор"
    text: "редактор"
""")
    config = load_config(path)
    assert config.queries[0].role == "Автор"


def test_explicit_role_is_kept(tmp_path):
    path = _write(tmp_path, """
sources: [hh]
queries:
  - name: "Автор (удалёнка)"
    role: author
    text: "редактор"
""")
    assert load_config(path).queries[0].role == "author"


def test_role_must_be_a_string(tmp_path):
    path = _write(tmp_path, """
sources: [hh]
queries:
  - name: "Автор"
    role: 42
    text: "редактор"
""")
    with pytest.raises(ConfigError, match="role"):
        load_config(path)


def test_empty_role_is_an_error(tmp_path):
    path = _write(tmp_path, """
sources: [hh]
queries:
  - name: "Автор"
    role: "  "
    text: "редактор"
""")
    with pytest.raises(ConfigError, match="role"):
        load_config(path)
```

`_write` — существующий помощник в `tests/test_config.py`; если его нет, использовать тот, которым написаны соседние тесты.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/pytest tests/test_config.py -k role -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'role'` / `ConfigError` не поднимается.

- [ ] **Step 3: Добавить поле и валидацию**

В `hunter/models.py` после `text: str`:

```python
    # Роль для дайджеста и статистики. У роли два запроса — обычный и
    # текстовый про удалёнку, — и оба должны попасть в один блок.
    role: str = ""
```

В `hunter/config.py`: `"role"` в `_QUERY_FIELDS`; после проверки `name`/`text`:

```python
        role = merged.get("role", merged["name"])
        if not isinstance(role, str) or not role.strip():
            raise ConfigError(
                f"Запрос №{number}: role — непустая строка, получено: {role!r}"
            )
        merged["role"] = role
```

- [ ] **Step 4: Прогнать тесты конфига**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Написать падающие тесты склейки ролей**

В `tests/test_run.py`:

```python
def test_two_queries_of_one_role_make_one_digest_block():
    store = Store(":memory:")
    config = Config(sources=("hh",), queries=(
        SearchQuery(name="Автор (удалёнка)", role="author", text="python"),
        SearchQuery(name="Автор (текст)", role="author", text="python remote"),
    ))
    sender = Sender()
    code = run_pass(
        config=config, store=store,
        fetch_page=lambda url: _scored_page(1, 2),
        send=sender, now=NOW, pause=no_pause,
    )
    text = "\n".join(sender.messages)
    assert code == 0
    assert text.count("<b>author</b>") == 1
    assert "Автор (удалёнка)" not in text


def test_hits_record_the_role_not_the_query_name():
    store = Store(":memory:")
    config = Config(sources=("hh",), queries=(
        SearchQuery(name="Промо", role="promo", text="smm"),
    ))
    run_pass(
        config=config, store=store, fetch_page=lambda url: _scored_page(1),
        send=Sender(), now=NOW, pause=no_pause,
    )
    rows = store.corpus._conn.execute("SELECT role, query FROM hits").fetchall()
    assert rows == [("promo", "smm")]
```

- [ ] **Step 6: Убедиться, что они падают**

Run: `.venv/bin/pytest tests/test_run.py -k "role" -v`
Expected: FAIL — блок назван именем запроса / в `hits.role` лежит имя запроса.

- [ ] **Step 7: Реализовать склейку и запись роли**

В `hunter/run.py`:

```python
def _merge_by_role(groups: list[Group], queries: tuple[SearchQuery, ...]) -> list[Group]:
    """У роли два запроса — обычный и текстовый про удалёнку. В дайджесте
    должен быть один блок, поэтому группы склеиваются по роли."""
    role_by_name = {q.name: q.role for q in queries}
    order = list(dict.fromkeys(q.role for q in queries))
    by_role: dict[str, list[Vacancy]] = {role: [] for role in order}
    for name, vacancies in groups:
        by_role[role_by_name[name]].extend(vacancies)
    return [(role, by_role[role]) for role in order if by_role[role]]
```

`_note_in_corpus` принимает `dict[str, SearchQuery]` и пишет `add_hit(row["id"], queries[name].role, queries[name].text)`.

В `run_pass` после `_note_in_corpus` и до оценки:

```python
        by_name = {q.name: q for q in config.queries}
        _note_in_corpus(store, fresh, by_name)
    fresh = _merge_by_role(fresh, config.queries)
```

(строка `fresh = _merge_by_role(...)` — вне `if not dry_run`.)

Импорт `SearchQuery` в `run.py` уже есть через `from .models import Score, Vacancy` — добавить.

- [ ] **Step 8: Прогнать тесты**

Run: `.venv/bin/pytest tests/test_run.py tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 9: Коммит**

```bash
git add hunter/models.py hunter/config.py hunter/run.py tests/test_config.py tests/test_run.py
git commit -m "feat: группировать дайджест по роли, а не по имени запроса"
```

---

### Task 2: Блоки «Рядом» и «Без оценки» в дайджесте

**Files:**
- Modify: `hunter/config.py:28-46`, `hunter/config.py:161-180`
- Modify: `hunter/digest.py:138-221`
- Test: `tests/test_config.py`, `tests/test_digest.py`

**Interfaces:**
- Consumes: `Score`, `Vacancy`, `Group`.
- Produces:
  - `ScoringConfig.nearby_threshold: int` (по умолчанию 3).
  - `render(..., nearby: Sequence[Vacancy] = (), unscored: Sequence[Vacancy] = (), nearby_limit: int = 40, unscored_limit: int = 20)`.
  - `render_console(..., nearby: Sequence[Vacancy] = (), unscored: Sequence[Vacancy] = ())`.

- [ ] **Step 1: Написать падающие тесты конфига**

```python
def test_nearby_threshold_defaults_to_three(tmp_path):
    config = load_config(_scoring_config(tmp_path))
    assert config.scoring.nearby_threshold == 3


def test_nearby_threshold_above_threshold_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="nearby_threshold"):
        load_config(_scoring_config(tmp_path, nearby=6))
```

`_scoring_config` — помощник, которым уже проверяются `threshold`/`model` в этом файле; добавить `nearby_threshold` в YAML‑строку.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/pytest tests/test_config.py -k nearby -v`
Expected: FAIL — неизвестное поле `nearby_threshold`.

- [ ] **Step 3: Реализовать поле**

`ScoringConfig`: `nearby_threshold: int = 3` после `threshold`. В `_SCORING_FIELDS` добавить `"nearby_threshold"`. Валидация после `threshold`:

```python
    nearby = raw.get("nearby_threshold", 3)
    if isinstance(nearby, bool) or not isinstance(nearby, int) or not 1 <= nearby <= threshold:
        raise ConfigError(
            f"scoring: nearby_threshold — число от 1 до {threshold}, получено: {nearby!r}"
        )
```

- [ ] **Step 4: Написать падающие тесты дайджеста**

```python
def test_nearby_block_shows_score_why_and_no_header_when_empty():
    vacancy = make_vacancy(name="Редактор", company="Рунити")
    text = render_console(
        [], scores={"hh:134991226": Score(3, "роль та, нет кейсов B2B")},
        below=2, threshold=5, warnings=(),
        nearby=[vacancy], unscored=[],
    )
    assert "Рядом" in text
    assert "3 · Редактор — Рунити" in text
    assert "роль та, нет кейсов B2B" in text


def test_nearby_is_capped_at_forty_in_telegram():
    vacancies = [make_vacancy(vacancy_id=str(i)) for i in range(41)]
    scores = {v.key: Score(4, "рядом") for v in vacancies}
    messages = render([], NOW, scores=scores, nearby=vacancies)
    text = "\n".join(messages)
    assert "ещё 1 в базе" in text
    assert text.count('<a href=') == 40


def test_exactly_forty_has_no_remainder_line():
    vacancies = [make_vacancy(vacancy_id=str(i)) for i in range(40)]
    scores = {v.key: Score(4, "рядом") for v in vacancies}
    text = "\n".join(render([], NOW, scores=scores, nearby=vacancies))
    assert "ещё" not in text
    assert text.count('<a href=') == 40


def test_unscored_block_is_separate_from_passed():
    vacancy = make_vacancy()
    text = render_console(
        [], scores={vacancy.key: Score(None, "модель не ответила")},
        below=0, threshold=5, warnings=(), nearby=[], unscored=[vacancy],
    )
    assert "Без оценки" in text
    assert "? · Senior Python Developer" in text


def test_nearby_and_unscored_are_full_in_console():
    vacancies = [make_vacancy(vacancy_id=str(i)) for i in range(45)]
    scores = {v.key: Score(3, "рядом") for v in vacancies}
    text = render_console(
        [], scores=scores, below=0, threshold=5, warnings=(),
        nearby=vacancies, unscored=[],
    )
    assert "ещё" not in text
```

`NOW` уже определён в `tests/test_digest.py`; если нет — `datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)`.

- [ ] **Step 5: Убедиться, что тесты падают**

Run: `.venv/bin/pytest tests/test_digest.py -k "nearby or unscored" -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'nearby'`.

- [ ] **Step 6: Реализовать блоки**

В `digest.py` — помощник строки «рядом» и «без оценки»:

```python
def _format_brief(vacancy: Vacancy, score: Score | None, *, html: bool) -> str:
    """Одна строка для серой зоны: оценка, название, компания, з/п, ссылка, причина."""
    label = _score_label(score)
    why = f" — {score.why}" if score is not None and score.why else ""
    if html:
        name = f'<a href="{escape(vacancy.url)}">{escape(vacancy.name)}</a>'
        return f"• {label} · {name} — {escape(vacancy.company)} · {escape(format_salary(vacancy))}{escape(why)}"
    return (
        f"  {label} · {vacancy.name} — {vacancy.company} · "
        f"{format_salary(vacancy)} · {_console_url(vacancy)}{why}"
    )
```

`render`: после основного блока групп добавить

```python
    if nearby:
        shown = nearby[:nearby_limit]
        body = "\n".join(_format_brief(v, scores.get(v.key), html=True) for v in shown)
        block = f"<b>Рядом ({len(nearby)})</b>\n{body}"
        if len(nearby) > nearby_limit:
            block += f"\nещё {len(nearby) - nearby_limit} в базе"
        blocks.append(block)
    if unscored:
        shown = unscored[:unscored_limit]
        ...
        block = f"<b>Без оценки ({len(unscored)})</b>\n{body}"
```

`render_console`: два таких же блока без потолка, заголовки `Рядом (N)` и `Без оценки (N)`, строки через `_format_brief(..., html=False)`.

- [ ] **Step 7: Прогнать тесты**

Run: `.venv/bin/pytest tests/test_digest.py tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 8: Коммит**

```bash
git add hunter/config.py hunter/digest.py tests/test_config.py tests/test_digest.py
git commit -m "feat: блоки «Рядом» и «Без оценки» в дайджесте"
```

---

### Task 3: Маршрутизация вакансий в `run_pass`

**Files:**
- Modify: `hunter/run.py:206-262`, `hunter/run.py:274-279`
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: `render(..., nearby=..., unscored=...)`, `render_console(...)`.
- Produces: `_route(score: Score | None, threshold: int, nearby_threshold: int) -> str` — `"passed"`, `"nearby"`, `"unscored"` или `"low"`.

- [ ] **Step 1: Переписать тест, который закреплял старое поведение**

Заменить `test_unscored_and_card_scores_bypass_the_threshold` на:

```python
def test_card_score_below_threshold_goes_to_nearby_not_the_main_list():
    store = Store(":memory:")
    score = FakeScore({"hh:1": Score(7, "да"), "hh:2": Score(2, "по карточке", from_card=True)})

    _, text = _scored_pass(store, score)

    assert "• 7 · " in text
    assert "Рядом (1)" in text
    assert "2* · Вакансия 2" in text


def test_unscored_goes_to_its_own_block():
    store = Store(":memory:")
    score = FakeScore({"hh:1": Score(7, "да"), "hh:2": Score(None, "модель не ответила")})

    _, text = _scored_pass(store, score)

    assert "Без оценки (1)" in text
    assert "? · Вакансия 2" in text


def test_low_scores_are_counted_only():
    store = Store(":memory:")
    score = FakeScore({"hh:1": Score(7, "да"), "hh:2": Score(1, "нет")})

    _, text = _scored_pass(store, score)

    assert "ещё 1 ниже 5" in text
    assert "1 · Вакансия 2" not in text


def test_nearby_threshold_comes_from_the_argument():
    store = Store(":memory:")
    score = FakeScore({"hh:1": Score(5, "да"), "hh:2": Score(3, "рядом")})

    _, text = _scored_pass(store, score, nearby_threshold=2)

    assert "Рядом (1)" in text


def test_scoring_crash_sends_all_to_the_unscored_block():
    store = Store(":memory:")

    def boom(vacancies):
        raise RuntimeError("quota")

    _, text = _scored_pass(store, boom)

    assert "Без оценки (2)" in text
    assert "<a href=" not in text
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/pytest tests/test_run.py -k "nearby or unscored or low_scores" -v`
Expected: FAIL — `nearby_threshold` не известен, «рядом» нет.

- [ ] **Step 3: Реализовать маршрутизацию**

```python
def _route(score: Score | None, threshold: int, nearby_threshold: int) -> str:
    """Куда вакансия идёт в дайджесте. Оценка по карточке ненадёжна, но
    решение принимается по её значению: звёздочка в подписи это показывает."""
    if score is None or not score.scored:
        return "unscored"
    if score.value >= threshold:
        return "passed"
    if score.value >= nearby_threshold:
        return "nearby"
    return "low"
```

В `run_pass` добавить параметр `nearby_threshold: int = 3`; заменить блок оценки:

```python
        nearby: list[Vacancy] = []
        unscored: list[Vacancy] = []
        low = 0
        for name, vacancies in fresh:
            passed = []
            for vacancy in vacancies:
                route = _route(scores.get(vacancy.key), threshold, nearby_threshold)
                if route == "passed":
                    passed.append(vacancy)
                elif route == "nearby":
                    nearby.append(vacancy)
                elif route == "unscored":
                    unscored.append(vacancy)
                else:
                    low += 1
            if passed:
                digest_groups.append((name, passed))
        nearby.sort(key=lambda v: (-scores[v.key].value, -v.published_at.timestamp()))
```

`below=len(below)` во всех вызовах заменить на `low`, `render`/`render_console` получают `nearby=nearby, unscored=unscored`. В `--dry-run` после печати «Ниже порога» печатать так же «Рядом» и «Без оценки» (по одной строке на вакансию, формат как в консоли).

`_passes` удалить; `main` передаёт `nearby_threshold=config.scoring.nearby_threshold if config.scoring else 3`.

- [ ] **Step 4: Прогнать весь набор**

Run: `.venv/bin/pytest -q`
Expected: PASS. Падения в тестах, ожидающих «ещё N ниже 5» при оценке 3, поправить: теперь такие вакансии в «рядом».

- [ ] **Step 5: Коммит**

```bash
git add hunter/run.py tests/test_run.py
git commit -m "feat: раскладывать вакансии на прошедшие, рядом, без оценки и ниже"
```

---

### Task 4: Личный конфиг и профиль

**Files:**
- Modify: `me/config.yaml`
- Modify: `me/profile.md`

**Interfaces:**
- Consumes: `role`, `nearby_threshold` из задач 1–2.
- Produces: восемь запросов, четыре роли.

- [ ] **Step 1: Переписать `me/config.yaml`**

`defaults`: `search_period: 7`, `max_pages` убрать (задаётся в каждом запросе). Запросы — по два на роль; у текстового варианта `work_format: null` (снимает `remote` из defaults) и хвост про удалёнку:

```
 AND (удаленно OR удалённо OR удаленка OR удалёнка OR "из дома" OR дистанционно)
```

Тексты:

1. `author` / «Автор и редактор (удалёнка)»: `NAME:(автор OR копирайтер OR редактор OR райтер OR "контент-райтер" OR "контент-маркетолог" OR "контент маркетолог" OR "контент-редактор" OR "шеф-редактор" OR "контент-стратег" OR "контент-директор" OR "head of content" OR новостник OR "редактор новостей" OR "новостной редактор" OR "редактор ленты" OR "news editor" OR "редактор телеграм-канала") NOT NAME:(медицинский OR юридического OR сценарист OR переводчик OR "технический писатель" OR UX OR студенческих OR курсов OR методист OR "обучения нейросетей" OR вечерний OR маркетплейсов OR "карточек товаров" OR видео OR отзывов OR корректор OR "онлайн-школу" OR "онлайн школы")`, `max_pages: 4`. Хвост `AND (хабр OR … B2B …)` удалён, `новости`/`новостник` из `NOT` убраны — роль новостника владельцу подходит.
2. `author` / «Автор и редактор (удалёнка в тексте)»: тот же текст + хвост про удалёнку, `work_format: null`, `max_pages: 4`.
3. `vibecoder` / «Вайбкодер и AI-автоматизация (удалёнка)»: текущий текст + `OR автоматизатор OR интегратор OR "автоматизация процессов"`, `max_pages: 3`.
4. `vibecoder` / «Вайбкодер (удалёнка в тексте)»: тот же + хвост, `work_format: null`, `max_pages: 3`.
5. `content-ai` / «Контент + ИИ (удалёнка)»: текущий текст, `max_pages: 3`.
6. `content-ai` / «Контент + ИИ (удалёнка в тексте)»: тот же + хвост, `work_format: null`, `max_pages: 3`.
7. `promo` / «Продвижение (удалёнка)»: `NAME:(SMM OR "SMM-менеджер" OR "SMM-специалист" OR таргетолог OR "таргетированная реклама" OR "контекстная реклама" OR "яндекс директ" OR директ OR PPC OR медиабайер OR "media buyer" OR "трафик-менеджер" OR performance) NOT NAME:(продаж OR маркетплейс OR монтаж)`, `max_pages: 6`.
8. `promo` / «Продвижение (удалёнка в тексте)»: тот же + хвост, `work_format: null`, `max_pages: 6`.

В `scoring` добавить `nearby_threshold: 3`. Старые запросы «AI-специалист», «ИИ-инструменты в тексте», «Боты и технический помощник» удалить: их покрывают `vibecoder` и `content-ai`.

- [ ] **Step 2: Проверить конфиг**

Run: `.venv/bin/python -c "from hunter.config import load_config; c=load_config('me/config.yaml'); print([(q.role, q.name) for q in c.queries])"`
Expected: восемь пар, роли по две.

- [ ] **Step 3: Поправить `me/profile.md`**

Заменить строки:

- `От **80 тыс./мес** на полную занятость (с 08.10; раньше называл 70). Частичная — может быть меньше.` → `От **60 тыс./мес** на полную занятость. Проекты и частичная занятость считаются по объёму: 30 тыс. за проект на две недели — нормально.`
- `- Деньги: от 80 тыс. на полную занятость; частичная может быть меньше.` → `- Деньги: от 60 тыс. на полную занятость; проекты и частичная — по объёму.`
- `> Общие рамки для всех ролей: только удалёнка; от 70 тыс./мес; любая занятость` → `> Общие рамки для всех ролей: только удалёнка; от 60 тыс./мес на полную занятость; проекты и частичная — по объёму` (следующую строку `> (полная, частичная, проектная, сдельная).` оставить).
- `- джун-позиции и зарплаты ниже 70 тыс. (Topface, 40–60 тыс.);` → `- джун-позиции и зарплаты ниже 60 тыс. на полной занятости (Topface, 40–60 тыс.);`
- `- SMM с упором на съёмку, монтаж и визуал (Meifort);` → `- съёмка и монтаж видео, визуальный SMM (Meifort);`
- `- журналистика: сеть источников в отрасли, ежедневные новости (RUVIERA);` — удалить из «Нет» роли `author`.
- После последнего пункта блока «Да» роли `author` (`- контент-маркетинг SaaS с оркестрацией нейросетей (Самозанятые.рф).`) добавить: `- новостная лента с ИИ-пайплайнами: собираю новости сам, чистовик отсматриваю (сейчас строю свою ИИ-новостную службу);`.
- `- трафик с бюджетами, тимлидство, английский (Provod.ai).` → `- крупные рекламные бюджеты и ответственность за CAC, тимлидство, английский (Provod.ai); небольшие кампании вместе с контентом — роль `promo`.`

Удалить блок:

```
### Вне поиска
- **Реклама** (Директ, TG Ads) как отдельная роль: требуют кейсы с бюджетами.
  Это навык в довесок к ролям выше. Оба вердикта «нет».
```

Вместо него — новая роль после «Роль 3»:

```markdown
### Роль 4. `promo` — продвижение контента

SMM и реклама как довесок к контенту: ведёт кампании вместе с текстами,
отвечает за трафик и заявки на небольших бюджетах.

**Да:**
- SMM с текстами, контентом и продвижением;
- Яндекс Директ и TG Ads на небольших бюджетах вместе с контентом;
- таргет и трафик, где не требуют многолетних кейсов.

**Нет:**
- миллионные бюджеты и ответственность только за CAC;
- требование многолетних кейсов с цифрами;
- продажи рекламы, монтаж видео и съёмка;
- маркетплейсы.
```

- [ ] **Step 4: Проверить профиль**

Run: `grep -n "тыс\|Роль 4\|Вне поиска" me/profile.md`
Expected: 60 вместо 70/80 в трёх местах; «Роль 4» есть; «Вне поиска» нет.

- [ ] **Step 5: Коммит не нужен** — `me/` в `.gitignore`. Зафиксировать в отчёте.

---

### Task 5: Обвязка, сброс базы и первый сбор

**Files:**
- Modify: `hunter/corpus/GUIDE.md`
- Modify: `.claude/skills/collect/SKILL.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: всё вышеперечисленное.
- Produces: рабочий первый проход.

- [ ] **Step 1: GUIDE — запрос «рядом»**

В раздел с примерами `vacancies sql` добавить:

````markdown
«Рядом» после того, как вакансии помечены виденными:

```bash
vacancies sql "SELECT v.id, v.name, v.company, s.score, s.why
FROM latest_scores s JOIN vacancies v ON v.id = s.vacancy_id
WHERE s.score BETWEEN 3 AND 4 AND v.first_seen_at >= date('now','-7 days')
ORDER BY s.score DESC, v.published_at DESC LIMIT 100"
```
````

- [ ] **Step 2: collect — сводка с двумя списками**

В `SKILL.md` в описание ответа после сбора добавить: сводка в чат — сколько прошло порог, сколько «рядом» и сколько без оценки; перечень «рядом» с оценками и причинами; на «покажи рядом» — SQL из GUIDE.

- [ ] **Step 3: README — одна строка**

В описание этапа сбора: «прошедшие порог — в основном списке, 3–4 балла — блоком «Рядом», без оценки — отдельным блоком».

- [ ] **Step 4: Прогнать тесты и проверить конфиг**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 5: Сбросить базу**

```bash
mkdir -p me/archive/reset-2026-11-03-2
mv me/job-hunter.db me/archive/reset-2026-11-03-2/job-hunter.db
.venv/bin/vacancies init
```

- [ ] **Step 6: Пробный проход без оценки**

Run: `.venv/bin/hunter --dry-run --no-score`
Expected: восемь блоков ролей, суммарно 600–700 карточек. Меньше 400 — смотреть `max_pages` и `NOT`.

- [ ] **Step 7: Боевой первый проход**

Run в фоне: `.venv/bin/hunter --dry-run` (с оценкой, 40–70 минут), затем разбор: сколько прошло, сколько рядом, без оценки. После проверки — обычный проход без `--dry-run`, чтобы пометить виденными.

- [ ] **Step 8: Коммит документации**

```bash
git add hunter/corpus/GUIDE.md .claude/skills/collect/SKILL.md README.md
git commit -m "docs: два списка в дайджесте и SQL для «рядом»"
```
