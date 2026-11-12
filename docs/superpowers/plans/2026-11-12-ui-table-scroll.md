# Таблица вакансий: одна прокрутка — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Страница `vacancies ui` прокручивается только внутри таблицы: пустого места под ней больше нет.

**Architecture:** Раскладка страницы становится flex-колонкой на высоту окна, таблица забирает остаток и прокручивается сама; спан `.sr-only` в ячейке оценки заменяется на `aria-label` у `<td>`, чтобы абсолютно позиционированный элемент не растягивал документ.

**Tech Stack:** Python 3, стандартная библиотека (`http.server`, строковые шаблоны), pytest, headless Chrome для замера раскладки.

**Spec:** `docs/superpowers/specs/2026-11-12-ui-table-scroll-design.md`

## Global Constraints

- Вся правка — в `hunter/corpus/ui.py`; сторонних зависимостей, сборки и SPA не появляется (проектное правило #27).
- Тексты на странице — русские, как сейчас.
- Тесты гоняются из корня репозитория: `.venv/bin/python -m pytest tests/test_corpus_ui.py -v` (bare `python3` в этом репозитории не годится: `hunter.export` тянет openpyxl).
- Замеры раскладки — только `google-chrome --headless=new` (стоит в `/usr/bin/google-chrome`).
- Спека — источник истины по значениям: `min-height: 240px` у таблицы, `height: 100vh` у `body`, `aria-label="без оценки"` в ячейке.

## Review Focus

- Пустой результат фильтров: в `tbody` одна строка «Ничего не найдено», таблица всё равно тянется на остаток окна, счётчик «Показано 0 из N» под ней. Тест — в Task 1 (`test_empty_page_keeps_layout_contract`).
- Очень низкое окно (меньше ~500 px по высоте): таблица не схлопывается ниже 240 px, страница подкручивается на недостающее, счётчик достижим. Проверка — замер в Task 2 при окне 800×480.
- Широкая таблица: горизонтальная прокрутка остаётся у `.wrap`, страница по горизонтали не прокручивается. Проверка — `docScrollWidth == innerWidth` в замере Task 2.
- Закреплённая шапка внутри прокручиваемого блока: `th` остаётся `position: sticky`, при прокрутке таблицы шапка на месте. Проверка — в CSS Task 2 правило не меняется; в скриншоте шапка видна при прокрученной таблице.
- Доступность ячейки без оценки: скринридер читает «без оценки», а не тире. Тест — в Task 1 (`test_unscored_cell_is_labelled`).

---

### Task 1: Ячейка без оценки через `aria-label`

**Files:**
- Modify: `hunter/corpus/ui.py:309-321` (`_score_cell`)
- Test: `tests/test_corpus_ui.py:157-169` (`test_score_class_follows_threshold`), новые тесты рядом

**Interfaces:**
- Consumes: `_score_cell(score, threshold: int) -> str` — как есть.
- Produces: разметка неоценённой ячейки
  `<td class="score none" aria-label="без оценки"><span aria-hidden="true">—</span></td>`;
  спанов `class="sr-only"` внутри таблицы больше нет (`.sr-only` остаётся только у `<caption>`).

- [ ] **Step 1: Обновить существующий тест и добавить два новых**

В `tests/test_corpus_ui.py` в `test_score_class_follows_threshold` строку 169
`assert 'class="score none"><span aria-hidden="true">—</span>' in page`
заменить на
`assert 'class="score none" aria-label="без оценки"><span aria-hidden="true">—</span>' in page`.

Ниже добавить:

```python
def test_unscored_cell_is_labelled(corpus):
    _add(corpus, 1)
    page = _page(corpus)
    assert 'aria-label="без оценки"' in page
    assert '<span class="sr-only">без оценки</span>' not in page


def test_empty_page_keeps_layout_contract(corpus):
    page = _page(corpus, filters=Filters(q="нет такой вакансии"))
    assert "Ничего не найдено" in page
    assert 'class="wrap"' in page
    assert 'class="count"' in page
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `.venv/bin/python -m pytest tests/test_corpus_ui.py -k "score_class or unscored_cell or empty_page" -v`
Expected: FAIL — в разметке пока `class="score none"><span aria-hidden="true">—</span><span class="sr-only">без оценки</span>`.

- [ ] **Step 3: Переписать `_score_cell`**

В `hunter/corpus/ui.py` ветку для `score is None` заменить на:

```python
    if score is None:
        return (
            '<td class="score none" aria-label="без оценки">'
            '<span aria-hidden="true">—</span></td>'
        )
```

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/python -m pytest tests/test_corpus_ui.py -v`
Expected: PASS, все тесты модуля зелёные.

- [ ] **Step 5: Коммит**

```bash
git add hunter/corpus/ui.py tests/test_corpus_ui.py
git commit -m "fix: ячейка без оценки помечается aria-label вместо sr-only"
```

---

### Task 2: Раскладка на высоту окна, одна прокрутка

**Files:**
- Modify: `hunter/corpus/ui.py:145-146` (`body`, `h1`), `:149` (`.filters`), `:154` (`.wrap`), `:182` (`.count`) — всё внутри `_STYLE`

**Interfaces:**
- Consumes: разметку из `render_page` (`h1`, `.filters`, `.wrap`, `.count`) — не меняется.
- Produces: CSS-контракт страницы: `body` — flex-колонка высотой `100vh` с `overflow: auto`;
  `.wrap` — `flex: 1 1 auto; min-height: 240px; position: relative; overflow: auto`;
  `max-height: calc(100vh - 200px)` из кода исчезает.

- [ ] **Step 1: Переписать правила в `_STYLE`**

Три правила заменяются целиком, остальные не трогаются:

```css
body { margin: 0; padding: 24px; height: 100vh; display: flex; flex-direction: column; overflow: auto; font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; color: #1f2328; background: #f6f7f9; }
h1 { flex: none; margin: 0 0 16px; font-size: 20px; }
.filters { flex: none; display: flex; flex-wrap: wrap; gap: 12px; align-items: end; margin-bottom: 16px; padding: 12px; background: #fff; border: 1px solid #d8dee4; border-radius: 8px; }
.wrap { flex: 1 1 auto; min-height: 240px; position: relative; overflow: auto; background: #fff; border: 1px solid #d8dee4; border-radius: 8px; }
.count { flex: none; margin-top: 12px; color: #57606a; }
```

`th { position: sticky; top: 0; z-index: 1; … }` остаётся без изменений.

- [ ] **Step 2: Подготовить замер раскладки**

Сохранить в `/tmp/opencode/measure_ui.py`:

```python
"""Замер раскладки страницы vacancies ui в headless Chrome."""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/alex/projects/personal/vacancy-hunter")
sys.path.insert(0, str(ROOT))

import yaml

from hunter.corpus.store import Corpus
from hunter.corpus.ui import Filters, render_page, select_rows
from hunter.export import export_rows

PROBE = """
<script>
window.addEventListener("load", function () {
  var wrap = document.querySelector(".wrap");
  var count = document.querySelector(".count");
  document.title = JSON.stringify({
    innerHeight: innerHeight,
    innerWidth: innerWidth,
    docScrollHeight: document.documentElement.scrollHeight,
    docScrollWidth: document.documentElement.scrollWidth,
    wrapClient: wrap.clientHeight,
    wrapScroll: wrap.scrollHeight,
    countBottom: Math.round(count.getBoundingClientRect().bottom + scrollY)
  });
});
</script>
"""

config = yaml.safe_load((ROOT / "me/config.yaml").read_text())
threshold = config["scoring"]["threshold"]

with Corpus.open_readonly(ROOT / "me/job-hunter.db") as corpus:
    rows = export_rows(corpus, None)
selected = select_rows(rows, Filters())
page = render_page(selected, filters=Filters(), threshold=threshold, total=len(rows))
html = page.replace("</body>", PROBE + "</body>")
path = Path("/tmp/opencode/ui_measure.html")
path.write_text(html)

for width, height in ((1873, 952), (1280, 720), (1024, 600), (800, 480)):
    dom = subprocess.run(
        ["/usr/bin/google-chrome", "--headless=new", "--disable-gpu", "--no-sandbox",
         f"--window-size={width},{height}", "--virtual-time-budget=3000",
         "--dump-dom", path.as_uri()],
        capture_output=True, text=True, check=True,
    ).stdout
    title = re.search(r"<title>(.*?)</title>", dom, re.S)
    print(width, height, title.group(1) if title else "нет замера")
```

- [ ] **Step 3: Прогнать замер**

Run: `.venv/bin/python /tmp/opencode/measure_ui.py`

Expected для окон 1873×952, 1280×720, 1024×600: `docScrollHeight` равен `innerHeight`
(допуск ±1), `wrapClient` — остаток окна за вычетом шапки, фильтров и счётчика,
`docScrollWidth` равен `innerWidth`. Для 800×480: `wrapClient` не меньше 240,
`docScrollHeight` больше `innerHeight` на десятки пикселей — это ожидаемая
подкрутка страницы на низком окне.

- [ ] **Step 4: Снять скриншот и посмотреть глазами**

Run:
`/usr/bin/google-chrome --headless=new --disable-gpu --no-sandbox --window-size=1873,952 --virtual-time-budget=3000 --screenshot=/tmp/opencode/ui_shot.png file:///tmp/opencode/ui_measure.html`

Expected: на скриншоте фильтры сверху, таблица до низа окна со своей полосой
прокрутки, счётчик сразу под таблицей, серого пустого поля внизу нет.

- [ ] **Step 5: Прогнать тесты модуля**

Run: `.venv/bin/python -m pytest tests/test_corpus_ui.py -v`
Expected: PASS (CSS тестами не покрыт, проверяется тем, что страница рисуется и сохранение работает).

- [ ] **Step 6: Коммит**

```bash
git add hunter/corpus/ui.py
git commit -m "fix: таблица вакансий прокручивается одна, страница не растягивается"
```
