# Оценка вакансий через OpenCode — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** оценку вакансий можно гонять через OpenCode на бесплатной модели, а Claude Code остаётся вторым бэкендом; выбор — полем `scoring.backend` в конфиге. Пилот — одна пачка из 10 вакансий, не весь корпус.

**Architecture:** `hunter/scoring.py` получает второй транспорт `ask_opencode`: временная папка с `opencode.json`, агент `vacancy-scorer` с системным промптом и запретом инструментов, JSON-массив вакансий в stdin, JSONL-события на stdout. Общий диспетчер `ask_model` выбирает бэкенд по `Criteria.backend`. Остальной код (`score_vacancies`, `score_pass`, `run_pass`, `vacancies eval/rescore`) не меняет интерфейс `Ask`. В хеш критериев добавляется бэкенд, поэтому старые оценки не переиспользуются. `vacancies rescore --limit N` ограничивает пилот N самыми свежими вакансиями.

**Tech Stack:** Python 3.12, uv, pytest, `subprocess` → OpenCode CLI 2.2.4 (`opencode run`), Claude Code CLI как второй бэкенд.

**Spec:** `docs/superpowers/specs/2026-10-31-scoring-opencode-design.md`

## Global Constraints

- Новых зависимостей нет.
- Интерфейс `Ask = Callable[[str, str], object]` не меняется: и `run.py`, и `corpus/cli.py` передают в `score_pass`/`evaluate`/`rescore` функцию от двух аргументов.
- Обе ошибки бэкендов — один класс `ScoringError` (бывший `ClaudeError`). У ошибки есть признак `retryable`; ошибки квоты/лимита не повторяются.
- Одна повторная попытка на пачку для retryable-ошибок. Таймаут одного вызова — 600 с.
- Команда OpenCode ровно такая: `opencode run --agent vacancy-scorer --model <model> --format json`, payload — JSON-массив в stdin, `cwd` — временная папка вызова.
- Конфиг агента ровно такой (в `opencode.json` временной папки):
  ```json
  {
    "$schema": "https://opencode.ai/config.json",
    "agents": {
      "vacancy-scorer": {
        "mode": "primary",
        "hidden": true,
        "system": "<system prompt>",
        "steps": 1,
        "permissions": [{"action": "*", "resource": "*", "effect": "deny"}]
      }
    }
  }
  ```
- Промпт `_PROMPT`, `SCHEMA`, `DESCRIPTION_LIMIT`, `BATCH_SIZE`, порог по умолчанию и правило «оценка 1–10, выше — лучше» не меняются.
- `scoring.backend` — только `opencode` или `claude`; по умолчанию `opencode`. Модель по умолчанию: `opencode/big-pickle` для OpenCode, `sonnet` для Claude.
- `vacancies rescore --limit N` берёт N самых свежих (хвост сортировки `first_seen_at, id`), без флага поведение прежнее. Сеть не используется.
- Слой сессии (`CLAUDE.md`, `.claude/skills/`, хук) не трогаем.
- Все тесты без сети, без настоящих моделей, без реальных пауз; `run` подменяется фейком.
- Пользовательские тексты — по-русски, в стиле существующих сообщений. «Claude не оценил» → «модель не оценила», «подписка Claude» → «квота модели».

## Review Focus

1. **Квота не жжёт повтор.** Ошибка квоты (`Free usage limit reached`) приходит событием `error`; `ask_opencode` помечает ошибку `retryable=False`, `_ask_with_retry` не делает вторую попытку, остальные пачки не отправляются, вакансии получают «модель не оценила: …». (Tasks 2, 3, 5)
2. **Кривой ответ не роняет проход.** Битые строки JSONL пропускаются; пустой stdout, текст без JSON, ненулевой код — `ScoringError`, все вакансии пачки остаются без оценки с причиной, исключение не выходит наружу. (Task 3)
3. **Переключатель бэкенда работает в обе стороны.** По умолчанию вызывается `opencode`; при `backend: claude` — прежняя команда `claude -p` с той же моделью, без лишних вызовов. (Tasks 1, 3, 4)
4. **Кеш не смешивает бэкенды.** Бэкенд входит в `criteria_hash`; старые оценки остаются под прежним хешем и не используются. `rescore --limit` читает тексты из базы и не ходит в сеть. (Tasks 3, 5)
5. **Частичный ответ не теряет вакансии.** Нет id в ответе — вакансия остаётся неоценённой, а не пропадает; `--limit` не меняет сравнение «было → стало» для выбранных строк. (Tasks 3, 5)

---

## Карта файлов

| Файл | Что меняется |
|---|---|
| `hunter/config.py` | `ScoringConfig.backend`, допустимые значения, модель по умолчанию от бэкенда |
| `hunter/scoring.py` | `ScoringError`, `Criteria.backend`, хеш, `ask_opencode`, `ask_model`, разбор JSONL |
| `hunter/evaluate.py` | `rescore(..., limit=None)`, тексты сообщений |
| `hunter/run.py` | вызов `ask_model` вместо `ask_claude` |
| `hunter/corpus/cli.py` | `ask_model` в `eval`/`rescore`, `--limit`, тексты сообщений |
| `config.example.yaml` | `backend`, новая модель по умолчанию, пояснения |
| `README.md` | установка и описание оценки через OpenCode |
| `hunter/corpus/GUIDE.md` | `eval`, `rescore` (`--limit`), `scores`, «подписка Claude» → «квота модели» |
| `tests/test_config.py` | бэкенд, модель по умолчанию, допустимые поля |
| `tests/test_scoring.py` | `ScoringError`, отсутствие повтора квоты, `ask_opencode`, `ask_model`, хеш |
| `tests/test_evaluate.py` | monkeypatch `ask_model`, `ScoringError`, тесты `--limit` |
| `tests/test_run.py` | текст «модель не оценила» в фейковой оценке |
| `docs/superpowers/specs/2026-10-31-scoring-opencode-design.md` | закоммитить вместе с планом |

---

### Task 1: Бэкенд и модель в конфиге

**Files:**
- Modify: `hunter/config.py`, `config.example.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `ScoringConfig(profile: Path, threshold: int = 5, backend: str = "opencode", model: str = "opencode/big-pickle")` — frozen, slots.
  - `_BACKENDS = ("opencode", "claude")`, `_DEFAULT_MODELS = {"opencode": "opencode/big-pickle", "claude": "sonnet"}`.
  - `_SCORING_FIELDS` включает `"backend"`.
- Правила `_load_scoring`:
  - `backend = raw.get("backend", "opencode")`; не строка или не из `_BACKENDS` → `ConfigError("scoring: backend — opencode или claude, получено: ...")`.
  - `model = raw.get("model", _DEFAULT_MODELS[backend])`; пустая строка/не строка → прежняя ошибка про `model`.
  - `enabled: false` по-прежнему возвращает `None` до проверки `profile`.

- [ ] **Step 1: Тесты конфига**

В `tests/test_config.py`:
- Обновить `test_scoring_defaults`: `scoring.backend == "opencode"`, `scoring.model == "opencode/big-pickle"`.
- Добавить `test_scoring_claude_backend_defaults_to_sonnet`: `backend: claude` без `model` → `model == "sonnet"`.
- Добавить `test_scoring_model_overrides_the_backend_default`: `backend: claude`, `model: opus` → `model == "opus"`.
- Добавить `test_scoring_unknown_backend_rejected`: `backend: gpt` → `ConfigError`, в тексте `backend` и оба допустимых значения.
- В `test_scoring_unknown_keys_rejected` добавить `"backend"` в список ожидаемых допустимых полей.

- [ ] **Step 2: Прогнать — падают**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — у `ScoringConfig` нет `backend`, неизвестное поле `backend` отвергается.

- [ ] **Step 3: Реализовать**

Правки в `hunter/config.py` по интерфейсу выше. В `config.example.yaml` обновить закомментированную секцию:
```yaml
# Оценка вакансий моделью через OpenCode (по умолчанию) или Claude Code.
# Без этой секции бот работает как раньше: без текстов и оценки.
# scoring:
#   enabled: true
#   profile: profile.md
#   threshold: 5
#   backend: opencode          # opencode | claude
#   model: opencode/big-pickle # для backend: claude — например, sonnet
```

- [ ] **Step 4: Прогнать — проходят**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter/config.py config.example.yaml tests/test_config.py
git commit -m "feat(scoring): backend and model defaults in config"
```

---

### Task 2: `ScoringError` вместо `ClaudeError`, повтор только для retryable

**Files:**
- Modify: `hunter/scoring.py`
- Test: `tests/test_scoring.py`, `tests/test_evaluate.py`, `tests/test_run.py`

**Interfaces:**
- Produces:
  ```python
  class ScoringError(Exception):
      def __init__(self, message: str, *, retryable: bool = True):
          super().__init__(message)
          self.retryable = retryable
  ```
- `ask_claude` и `_ask_with_retry` бросают/ловят `ScoringError`.
- `_ask_with_retry`: если `not exc.retryable` — сразу `raise`, без второй попытки; иначе прежнее поведение (одна повторная попытка).
- Тексты: `"нет в ответе модели"`, `"модель не оценила: ..."`, лог `"Повтор запроса к модели: ..."`, комментарии про «модель недоступна».
- `ClaudeError` больше нигде не импортируется и не существует.

- [ ] **Step 1: Тесты**

В `tests/test_scoring.py`:
- Заменить импорт `ClaudeError` на `ScoringError`, все `raise ClaudeError(...)` — на `ScoringError(...)`.
- Переименовать `test_ask_claude_failures_raise_claude_error` → `test_ask_claude_failures_raise_scoring_error`.
- Добавить:
  ```python
  def test_non_retryable_error_is_not_retried(store):
      vs = vacancies(1, 2)

      class Quota:
          calls = 0
          def __call__(self, system, payload):
              self.calls += 1
              raise ScoringError("Free usage limit reached", retryable=False)

      ask = Quota()
      scores, warnings = score_vacancies(vs, _texts(vs), ask=ask, criteria=CRITERIA, store=store)
      assert ask.calls == 1, "ошибку квоты не повторяем"
      assert all(not s.scored for s in scores.values())
      assert "Free usage limit" in scores["hh:1"].why
      assert len(warnings) == 1
  ```
- В `tests/test_evaluate.py` заменить импорт `ClaudeError` на `ScoringError` и все `raise ClaudeError` на `ScoringError`; переименовать `test_claude_down_leaves_everything_unscored_with_reason` → `test_model_down_leaves_everything_unscored_with_reason` и такие же `..._with_claude_down_...` → `..._with_model_down_...`.
- В `tests/test_run.py` строку `Score(None, "Claude не оценил")` заменить на `Score(None, "модель не оценила")`.

- [ ] **Step 2: Прогнать — падают**

Run: `uv run pytest tests/test_scoring.py tests/test_evaluate.py -v`
Expected: FAIL — `ScoringError` не импортируется.

- [ ] **Step 3: Реализовать**

В `hunter/scoring.py`:
- Заменить класс и все упоминания `ClaudeError`.
- В `_ask_with_retry`:
  ```python
  except ScoringError as exc:
      if not exc.retryable or attempt >= ATTEMPTS:
          raise
      log.info("Повтор запроса к модели: %s", exc)
  ```
- В `score_vacancies` поправить `reason` и текст неоценённой вакансии.

- [ ] **Step 4: Прогнать — проходят, весь набор зелёный**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter/scoring.py tests/test_scoring.py tests/test_evaluate.py tests/test_run.py
git commit -m "refactor(scoring): rename ClaudeError to ScoringError with retry flag"
```

---

### Task 3: Бэкенд OpenCode и диспетчер `ask_model`

**Files:**
- Modify: `hunter/scoring.py`
- Test: `tests/test_scoring.py`

**Interfaces:**
- `Criteria` получает `backend: str = "opencode"`; порядок полей: `profile: str`, `model: str = "opencode/big-pickle"`, `backend: str = "opencode"`.
- `load_criteria(cfg)` передаёт `backend=cfg.backend`.
- `criteria_hash` добавляет `backend` в `parts` (рядом с `model`).
- Константы: `MODEL_TIMEOUT = 600` вместо `CLAUDE_TIMEOUT`, `OPENCODE_AGENT = "vacancy-scorer"`.
- Produces:
  ```python
  def ask_opencode(
      system: str, payload: str, *, model: str,
      timeout: float = MODEL_TIMEOUT, run=subprocess.run,
  ) -> object: ...

  def ask_model(
      system: str, payload: str, *, criteria: Criteria,
      run=subprocess.run,
  ) -> object: ...
  ```
  `ask_model`: `criteria.backend == "claude"` → `ask_claude`; `"opencode"` → `ask_opencode`; иначе `ValueError`.
- Внутренние помощники:
  - `_opencode_config(system: str) -> dict` — конфиг агента из Global Constraints.
  - `_parse_opencode(completed: subprocess.CompletedProcess) -> object` — разбор stdout.
  - `_extract_json_object(text: str) -> dict | None` — снять markdown-обёртку, взять от первой `{` до последней `}`, вернуть dict; если верхний уровень — массив строк оценок, обернуть в `{"items": [...]}`; иначе `None`.
- Разбор stdout:
  - каждая непустая строка — отдельный JSON-объект; битые строки пропускаются;
  - `type == "text"` и `part.text` — строка: накапливать;
  - `type == "error"`: `message = error.message` (или `error.type`); если есть `error.retryAfterMs` или `error.data.retryAfterMs` — добавить `" (повтор через ~N.N ч)"`; `retryable` = в сообщении нет подстрок `usage limit`, `quota`, `limit reached`, `credits` (без учёта регистра);
  - ошибка события важнее кода возврата; затем `returncode != 0` → `ScoringError` с `stderr`/`stdout` (excerpt 300);
  - пустой текст → `"opencode не вернул ответ: ..."`; текст без JSON → `"opencode не вернул структурированный ответ: ..."`.
- `ask_opencode` ловит `FileNotFoundError`, `subprocess.TimeoutExpired`, `OSError` и бросает `ScoringError` с русским текстом, как `ask_claude`.

- [ ] **Step 1: Тесты**

В `tests/test_scoring.py` добавить импорты `Path`, `ask_model`, `ask_opencode`, `ScoringError`, `load_criteria`; для теста `load_criteria` — `from hunter.config import ScoringConfig`. Тесты:

1. `test_criteria_hash_depends_on_the_backend`:
   ```python
   claude = Criteria(CRITERIA.profile, model="sonnet", backend="claude")
   opencode = Criteria(CRITERIA.profile, model="sonnet", backend="opencode")
   assert criteria_hash(claude) != criteria_hash(opencode)
   ```
2. `test_load_criteria_carries_backend` — `ScoringConfig(profile=tmp_path/"p.md", backend="claude", model="sonnet")`, файл с профилем; `load_criteria(...).backend == "claude"`.
3. `test_ask_opencode_builds_the_documented_command_and_agent_config` — фейковый `run` читает `Path(kwargs["cwd"])/"opencode.json"` во время вызова:
   - `args == ["opencode", "run", "--agent", "vacancy-scorer", "--model", "opencode/big-pickle", "--format", "json"]`;
   - `input == "[]"`;
   - `config["agents"]["vacancy-scorer"]` содержит `system == "система"`, `mode == "primary"`, `steps == 1`, `hidden is True`, `permissions == [{"action": "*", "resource": "*", "effect": "deny"}]`;
   - временная папка после вызова не существует.
4. `test_ask_opencode_joins_text_events_and_strips_fences` — два события `type: "text"`: первое содержит открывающую markdown-обёртку и начало JSON (`{"items": [`), второе — конец JSON (`]}`) и закрывающую обёртку; результат `{"items": []}`.
5. `test_ask_opencode_ignores_broken_jsonl_lines` — между событиями строка `не json`; результат тот же.
6. `test_ask_opencode_accepts_a_bare_array` — текст `[{"id": "hh:1", "score": 7, "why": "x", "flags": []}]` → `{"items": [...]}`.
7. `test_ask_opencode_error_event_raises_scoring_error` — событие `error` с `message: "Free usage limit reached"`, `retryAfterMs: 16_200_000`; `pytest.raises(ScoringError)`, `exc.retryable is False`, в тексте `"Free usage limit"` и `"повтор через"`.
8. `test_ask_opencode_failures_raise_scoring_error` — параметризованно: `FileNotFoundError`, `subprocess.TimeoutExpired`, пустой stdout, `"не json"`, текст без JSON, `returncode=1` со `stderr="boom"`.
9. `test_ask_model_dispatches_by_backend` — подменить `hunter.scoring.ask_claude` и `hunter.scoring.ask_opencode` фейками, вызвать `ask_model` с `Criteria(..., backend="claude")` и `backend="opencode"`, проверить порядок и переданную `model`.

- [ ] **Step 2: Прогнать — падают**

Run: `uv run pytest tests/test_scoring.py -v`
Expected: FAIL — нет `ask_opencode`, `ask_model`, `Criteria.backend`.

- [ ] **Step 3: Реализовать**

В `hunter/scoring.py`:
- Добавить `from pathlib import Path`.
- Переименовать `CLAUDE_TIMEOUT` → `MODEL_TIMEOUT`, поправить `ask_claude`.
- Добавить `backend` в `Criteria`, `load_criteria`, `criteria_hash`.
- Написать `_opencode_config`, `ask_opencode`, `_parse_opencode`, `_extract_json_object`, `ask_model`.
- В `ask_opencode`:
  ```python
  with tempfile.TemporaryDirectory(prefix="hunter-scorer-") as workdir:
      Path(workdir, "opencode.json").write_text(
          json.dumps(_opencode_config(system), ensure_ascii=False), encoding="utf-8"
      )
      command = ["opencode", "run", "--agent", OPENCODE_AGENT, "--model", model, "--format", "json"]
      completed = run(
          command, input=payload, capture_output=True, text=True,
          encoding="utf-8", timeout=timeout, cwd=workdir,
      )
      return _parse_opencode(completed)
  ```

- [ ] **Step 4: Прогнать — проходят**

Run: `uv run pytest tests/test_scoring.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter/scoring.py tests/test_scoring.py
git commit -m "feat(scoring): OpenCode backend behind a shared ask_model"
```

---

### Task 4: Проводка `ask_model` в проход и CLI

**Files:**
- Modify: `hunter/run.py`, `hunter/corpus/cli.py`
- Test: `tests/test_evaluate.py`

**Interfaces:**
- `hunter/run.py`: импорт `Criteria, ask_model, load_criteria, score_pass`; вызов:
  ```python
  ask=lambda system, payload: ask_model(system, payload, criteria=criteria),
  ```
- `hunter/corpus/cli.py`: импорт `ask_model, load_criteria`; в `_cmd_eval` и `_cmd_rescore` тот же лямбда-вызов.
- Пользовательские тексты в CLI: `"модель не оценила ни одной вакансии: ..."`, комментарии про частичный сбой модели; help `eval` — «сверить оценки модели с вердиктами владельца».

- [ ] **Step 1: Тесты**

В `tests/test_evaluate.py`:
- Все monkeypatch `"hunter.corpus.cli.ask_claude"` заменить на `"hunter.corpus.cli.ask_model"`; фейковые функции принимают `*, criteria` вместо `*, model`.
- В `test_eval_prints_report` фейк записывает `seen["backend"] = criteria.backend`, после вызова `assert seen["backend"] == "opencode"`.
- Добавить `test_eval_uses_claude_backend_from_config`: в конфиге `backend: claude`, `model: sonnet`; фейк `ask_model` записывает `criteria.backend` и `criteria.model`, возвращает оценки для трёх id; после `main(["eval", ...]) == 0` проверить `{"backend": "claude", "model": "sonnet"}`.

- [ ] **Step 2: Прогнать — падают**

Run: `uv run pytest tests/test_evaluate.py -v`
Expected: FAIL — CLI всё ещё зовёт `ask_claude`, monkeypatch не срабатывает.

- [ ] **Step 3: Реализовать**

Правки в `hunter/run.py` и `hunter/corpus/cli.py` по интерфейсу выше. В `cli.py` не забыть оба места — `eval` и `rescore`.

- [ ] **Step 4: Прогнать — проходят, весь набор зелёный**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter/run.py hunter/corpus/cli.py tests/test_evaluate.py
git commit -m "refactor(scoring): route runs and CLI through ask_model"
```

---

### Task 5: `vacancies rescore --limit N`

**Files:**
- Modify: `hunter/evaluate.py`, `hunter/corpus/cli.py`
- Test: `tests/test_evaluate.py`

**Interfaces:**
- `rescore(store: Store, *, ask: Ask, criteria: Criteria, days: int, limit: int | None = None)`.
- После запроса с `ORDER BY v.first_seen_at, v.id`:
  ```python
  if limit is not None:
      if limit < 1:
          raise ValueError("limit должен быть положительным")
      rows = rows[-limit:]
  ```
  То есть `limit` — хвост существующей сортировки, самые свежие.
- CLI `rescore`: `--limit` типа `int`, help «только N самых свежих вакансий (по умолчанию все)». Значение `< 1` → `print("--limit: число вакансий, от 1", file=sys.stderr)` и `EXIT_USAGE` (2). `limit=args.limit` передаётся в `rescore`.
- Без флага `args.limit is None` — прежнее поведение.

- [ ] **Step 1: Тесты**

В `tests/test_evaluate.py`:
- `test_rescore_limit_keeps_the_newest_vacancies` — `cached_database` с тремя вакансиями `days_ago` 3, 2, 1; `limit=1` → в результате только самая свежая (`vacancy_id == "3"`).
- `test_rescore_limit_prints_only_the_newest` — CLI с `--limit 2`; фейк оценивает `hh:4` и `hh:5`; в выводе есть «Осталась» и «Сдвинулась», нет «Поднялась» и «Упала»; `main(...) == 0`.
- `test_rescore_rejects_non_positive_limit` — `--limit 0` → код 2, в stderr есть `--limit`.

- [ ] **Step 2: Прогнать — падают**

Run: `uv run pytest tests/test_evaluate.py -v`
Expected: FAIL — `rescore()` не принимает `limit`, парсер не знает `--limit`.

- [ ] **Step 3: Реализовать**

Правки в `hunter/evaluate.py` и `hunter/corpus/cli.py` по интерфейсу выше. Обновить docstring `rescore`, чтобы упомянуть `limit`.

- [ ] **Step 4: Прогнать — проходят, весь набор зелёный**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hunter/evaluate.py hunter/corpus/cli.py tests/test_evaluate.py
git commit -m "feat(rescore): limit to the newest N vacancies"
```

---

### Task 6: Документация и вычистка упоминаний Claude в оценке

**Files:**
- Modify: `README.md`, `hunter/corpus/GUIDE.md`, `hunter/config.py`, `hunter/models.py`, `hunter/evaluate.py`, `hunter/corpus/cli.py`, `hunter/scoring.py`
- Add/commit: `docs/superpowers/specs/2026-10-31-scoring-opencode-design.md`, `docs/superpowers/plans/2026-10-31-scoring-opencode.md`

**Что именно:**
- `README.md`:
  - строка про «оценка Claude совпала» → «оценка по профилю совпала»;
  - установка: нужны uv, Claude Code для диалога и OpenCode для оценки; модель оценки — `scoring.model` в `me/config.yaml`, по умолчанию `opencode/big-pickle`; для возврата на Claude — `backend: claude`;
  - «повторный проход … подписку не тратит» → «квоту модели не тратит»;
  - в шпаргалку команд добавить `vacancies rescore --limit 10   # пилот на 10 самых свежих`.
- `hunter/corpus/GUIDE.md`:
  - `eval`: «Проверка оценки модели», «Тратит квоту модели (OpenCode или подписку Claude — смотря что в `scoring.backend`)», «модель не оценила ничего»;
  - `rescore`: сигнатура `[--days N] [--limit N] [--config PATH]`, абзац про `--limit`, «Тратит квоту модели»;
  - `scores`: «оценки модели по критериям», в описании `criteria_hash` — «критериев, модели и бэкенда».
- `hunter/config.py`, `hunter/models.py`, `hunter/evaluate.py`, `hunter/scoring.py`, `hunter/corpus/cli.py`: docstring/комментарии про «оценку Claude», «подписку Claude», «Claude не оценил» → «модель», «квота модели». Не трогать упоминания Claude как бэкенда (`backend: claude`, `ask_claude`) и слой сессии (`CLAUDE.md`, skills, «скажите Claude „давай настроим“»).

- [ ] **Step 1: Правки документации и комментариев**

Внести перечисленное выше.

- [ ] **Step 2: Аудит упоминаний**

Run: `grep -rn "Claude\|claude" README.md hunter/ config.example.yaml`
Expected: остались только Claude как второй бэкенд (`ask_claude`, `backend: claude`, `claude -p`), путь `CLAUDE.md`/skills и сообщение про настройку; нет «Claude не оценил», «подписка Claude» в смысле единственного способа оценки.

- [ ] **Step 3: Полный набор тестов**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md hunter/corpus/GUIDE.md hunter/config.py hunter/models.py \
  hunter/evaluate.py hunter/corpus/cli.py hunter/scoring.py \
  docs/superpowers/specs/2026-10-31-scoring-opencode-design.md \
  docs/superpowers/plans/2026-10-31-scoring-opencode.md
git commit -m "docs(scoring): OpenCode as the default scoring backend"
```

---

### Task 7: Пилот на одной пачке (после сброса бесплатной квоты)

Это проверка качества, а не код. Выполнять, когда `opencode` перестал отвечать ошибкой `Free usage limit reached` (в прошлый раз — примерно через 4,5 ч после 22:28 МСК 30 октября, то есть около 03:00 МСК 31 октября) или когда владелец подключил провайдера.

- [ ] **Step 1: Убедиться, что бэкенд и модель — OpenCode**

В `me/config.yaml` либо нет `scoring.backend` (действует `opencode`), либо стоит `backend: opencode`. Модель — `opencode/big-pickle` или другая бесплатная.

- [ ] **Step 2: Прогнать 10 самых свежих вакансий**

Run: `uv run vacancies rescore --limit 10`
Expected: 10 строк «было → стало · название — почему» (или меньше, если часть вакансий без текста/без изменений). При квоте — код 1 и «модель не оценила ни одной вакансии: Free usage limit reached…», без повторов и без зависания на 600 с.

- [ ] **Step 3: Владелец смотрит оценки глазами**

Критерий: оценки и причины правдоподобны, явных грубых ошибок нет. Это решение владельца, не агента.

- [ ] **Step 4: Сверить с архивом Claude по совпадающим вакансиям**

```bash
uv run vacancies sql "SELECT key, score, why FROM latest_scores ORDER BY scored_at DESC LIMIT 10"
uv run vacancies --db me/archive/reset-2026-10-31/job-hunter.db sql \
  "SELECT key, score, why FROM latest_scores ORDER BY scored_at DESC LIMIT 40"
```
Сопоставить по `key`/названию: где расхождение больше 2 баллов — записать в заметки. Это материал для решения, а не автоматический тест.

- [ ] **Step 5: Решение**

- Качество приемлемо → оставить `opencode`, дальше гонять обычный `hunter`/`vacancies rescore`.
- Качество плохое → в `me/config.yaml` поменять `scoring.model` на другую модель или `scoring.backend: claude` (модель `sonnet`) и повторить шаг 2.
- Квота кончилась на середине → дождаться сброса; частичные результаты уже в кеше и повторно не оплачиваются.

---

## Самопроверка плана

- [ ] Каждый пункт спеки покрыт: бэкенд в конфиге (Task 1), OpenCode-вызов и разбор (Task 3), `ScoringError` и отсутствие повтора квоты (Task 2), `ask_model` в проходе и CLI (Task 4), `rescore --limit` (Task 5), хеш с бэкендом (Task 3), тексты и документация (Task 6), пилот (Task 7).
- [ ] Нет задач, меняющих слой сессии, промпт, порог или платных провайдеров.
- [ ] У каждой кодовой задачи есть падающий тест до реализации, зелёный прогон после и отдельный коммит; Task 6 — документация, Task 7 — ручной пилот.
- [ ] Имена и сигнатуры совпадают между задачами: `ScoringError`, `Criteria.backend`, `ask_model(system, payload, *, criteria)`, `rescore(..., limit=None)`, `ScoringConfig.backend`.
- [ ] Review Focus ссылается на реальные тесты из задач.
