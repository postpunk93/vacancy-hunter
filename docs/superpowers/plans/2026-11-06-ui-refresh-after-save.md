# Обновление списка после сохранения — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** В `vacancies ui` после «Сохранить» страница сама пересобирает список, если этап строки изменился.

**Architecture:** Правка только в рендере и встроенном JS `hunter/corpus/ui.py`: форма строки получает `data-status` с этапом на момент рендера, скрипт сравнивает с ним значение селекта при отправке и после успешного ответа `/save` переходит на `back#v-<id>`. Сервер не меняется.

**Tech Stack:** Python 3.12, stdlib (`http.server`, `html`), ванильный JS в `_SCRIPT`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-06-ui-refresh-after-save-design.md`

## Global Constraints

- Новых зависимостей нет: `pyproject.toml` не меняется, только stdlib.
- Сервер слушает только `127.0.0.1`; маршруты и ответы `/save` не меняются.
- Любое значение из базы попадает в HTML только через `html.escape`.
- Интерфейс и комментарии в коде — по-русски.
- Тесты: `uv run pytest`.

## Review Focus

- Этап изменён, но `/save` вернул ошибку — переходить нельзя, остаётся текст ошибки в `.flash`.
- Этап вернули к исходному значению — перезагрузки быть не должно (сравнение с `data-status`, не с фактом отправки).
- Заметка сохранена без смены этапа — прежняя подсветка «сохранено», без перезагрузки.
- Без JS — прежний 303 на `back#v-<id>`.

## File Structure

- Modify: `hunter/corpus/ui.py` — `_row` (атрибут `data-status`), `_SCRIPT` (сравнение и переход).
- Test: `tests/test_corpus_ui.py` — новый тест на `data-status`.

---

### Task 1: `data-status` в форме строки и переход после смены этапа

**Files:**
- Modify: `hunter/corpus/ui.py:177-208` (`_SCRIPT`), `hunter/corpus/ui.py:309-333` (`_row`)
- Test: `tests/test_corpus_ui.py` (рядом с `test_page_has_columns_link_and_stages`)

**Interfaces:**
- Consumes: `Filters.query()` (скрытое поле `back` уже содержит текущие фильтры), `filters` и `_esc` из модуля.
- Produces: у каждой формы `form.save` атрибут `data-status` — текущий `row["status"]` или пустая строка для новой вакансии; `row.id` остаётся `v-<id>`.

- [ ] **Step 1: Write the failing test**

```python
def test_save_form_remembers_initial_status(corpus):
    _add(corpus, 1)
    _apply(corpus, 1, "applied", "отклик")
    _add(corpus, 2)
    page = _page(corpus)
    assert 'data-status="applied"' in page
    assert 'data-status=""' in page
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_corpus_ui.py::test_save_form_remembers_initial_status -v`
Expected: FAIL — `data-status` в разметке нет.

- [ ] **Step 3: Add `data-status` to the save form in `_row`**

Добавить в открывающий тег формы (после `class="save"`): `data-status="{_esc(_plain(row["status"]))}"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_corpus_ui.py::test_save_form_remembers_initial_status -v`
Expected: PASS

- [ ] **Step 5: Change `_SCRIPT` to reload on stage change**

В обработчике submit до `fetch` вычислить `var changed = stage.value !== form.dataset.status;`
(`stage = form.querySelector("select[name=status]")`). В ветке `response.ok`: если
`changed`, выполнить `window.location.href = form.querySelector("input[name=back]").value + "#" + row.id;`
и выйти; иначе — прежняя подсветка.

- [ ] **Step 6: Run the full UI test module**

Run: `uv run pytest tests/test_corpus_ui.py -v`
Expected: PASS, в том числе `test_no_js_redirects_to_same_view`, `test_page_keeps_filters_in_form_and_row_back`, `test_save_writes_status_and_note`.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add hunter/corpus/ui.py tests/test_corpus_ui.py docs/superpowers/specs/2026-11-06-ui-refresh-after-save-design.md docs/superpowers/plans/2026-11-06-ui-refresh-after-save.md
git commit -m "feat: обновлять список после смены этапа в таблице"
```
