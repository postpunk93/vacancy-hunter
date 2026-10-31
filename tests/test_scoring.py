import json
import subprocess

import pytest

from hunter.models import FullText, Score
from hunter.scoring import (
    DESCRIPTION_LIMIT,
    ScoringError,
    Criteria,
    ask_claude,
    build_system_prompt,
    criteria_hash,
    parse_scores,
    score_pass,
    score_vacancies,
    vacancy_payload,
)
from hunter.store import Store
from tests.factories import make_vacancy, vacancy_page

CRITERIA = Criteria(profile="## Что ищу\nавтор для Хабра\n\n## Красные флаги\nГПХ при полной")


def vacancies(*ids):
    return [make_vacancy(vacancy_id=str(i)) for i in ids]


class Ask:
    """Фейковый Claude: оценивает каждую вакансию из входа одной и той же
    цифрой, кроме перечисленных в skip; может всегда падать."""

    def __init__(self, score=7, skip=(), error=None):
        self.calls = []
        self.score = score
        self.skip = set(skip)
        self.error = error

    def __call__(self, system, payload):
        items = json.loads(payload)
        self.calls.append(items)
        if self.error:
            raise ScoringError(self.error)
        return {"items": [
            {"id": item["id"], "score": self.score, "why": "подходит", "flags": ["ГПХ"]}
            for item in items if item["id"] not in self.skip
        ]}


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "s.db") as s:
        yield s


def test_parse_scores_keeps_only_batch_ids_and_clamps():
    keys = ["hh:1", "hh:2", "hh:3", "hh:4", "hh:5"]
    data = {"items": [
        {"id": "hh:1", "score": 14, "why": "a", "flags": []},
        {"id": "hh:1", "score": 3, "why": "дубль", "flags": []},
        {"id": "hh:2", "score": 0.4, "why": "b", "flags": ["ГПХ", "", 5]},
        {"id": "hh:3", "score": True, "why": "c", "flags": []},
        {"id": "hh:4", "score": 6, "why": "  ", "flags": []},
        {"id": "hh:99", "score": 6, "why": "чужой", "flags": []},
    ]}
    scores = parse_scores(data, keys)

    assert scores == {
        "hh:1": Score(10, "a", ()),
        "hh:2": Score(1, "b", ("ГПХ",)),
    }


def test_parse_scores_survives_garbage():
    assert parse_scores(None, ["hh:1"]) == {}
    assert parse_scores({"items": "нет"}, ["hh:1"]) == {}
    assert parse_scores({"items": [None, 5]}, ["hh:1"]) == {}


def test_payload_truncates_description_and_marks_missing_text():
    vacancy = make_vacancy(vacancy_id="1")
    payload = vacancy_payload(vacancy, FullText("я" * 7000, ("SEO",)))

    assert payload["id"] == "hh:1"
    assert len(payload["description"]) == DESCRIPTION_LIMIT + 1
    assert payload["description"].endswith("…")
    assert payload["key_skills"] == ["SEO"]

    bare = vacancy_payload(vacancy, None)
    assert bare["description"] == ""
    assert bare["key_skills"] == []


def test_system_prompt_contains_the_profile_and_the_three_flags_rule():
    prompt = build_system_prompt(Criteria(profile="## Что ищу\nавтор"))
    assert "## Что ищу\nавтор" in prompt
    assert "три и больше тяжёлых признака — ниже 5" in prompt


def test_editing_profile_changes_the_hash():
    same = Criteria(CRITERIA.profile)
    other = Criteria(CRITERIA.profile + " и ещё")
    assert criteria_hash(same) == criteria_hash(CRITERIA)
    assert criteria_hash(other) != criteria_hash(CRITERIA)


def _completed(stdout, returncode=0, stderr=""):
    return subprocess.CompletedProcess(["claude"], returncode, stdout, stderr)


def test_ask_claude_builds_the_documented_command():
    seen = {}

    def run(args, **kwargs):
        seen["args"] = args
        seen.update(kwargs)
        return _completed(json.dumps({"is_error": False, "structured_output": {"items": []}}))

    ask_claude("система", "[]", model="sonnet", run=run)
    args = seen["args"]

    assert args[:2] == ["claude", "-p"]
    pairs = list(zip(args, args[1:]))
    for pair in (("--model", "sonnet"), ("--tools", ""), ("--setting-sources", ""),
                 ("--output-format", "json"), ("--system-prompt", "система")):
        assert pair in pairs
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--json-schema"):
        assert flag in args
    assert "--bare" not in args
    assert seen["input"] == "[]"


def test_ask_claude_returns_structured_output():
    def run(args, **kwargs):
        return _completed(json.dumps({"is_error": False, "structured_output": {"items": []}}))

    assert ask_claude("s", "[]", model="sonnet", run=run) == {"items": []}


def test_ask_claude_falls_back_to_result_json():
    def run(args, **kwargs):
        return _completed(json.dumps({"is_error": False, "result": '{"items": []}'}))

    assert ask_claude("s", "[]", model="sonnet", run=run) == {"items": []}


def _raise(exc):
    def run(args, **kwargs):
        raise exc
    return run


@pytest.mark.parametrize("run", [
    _raise(FileNotFoundError("claude")),
    _raise(subprocess.TimeoutExpired(["claude"], 600)),
    lambda args, **kw: _completed("не json"),
    lambda args, **kw: _completed(json.dumps({"is_error": False, "structured_output": {}}), returncode=1),
    lambda args, **kw: _completed(json.dumps({"is_error": True, "result": "лимит"})),
    lambda args, **kw: _completed(json.dumps({"is_error": False, "result": "просто текст"})),
])
def test_ask_claude_failures_raise_scoring_error(run):
    with pytest.raises(ScoringError):
        ask_claude("s", "[]", model="sonnet", run=run)


def _texts(vs):
    return {v.key: FullText("текст", ()) for v in vs}


def test_vacancies_are_sent_in_batches(store):
    vs = vacancies(*range(23))
    ask = Ask()
    score_vacancies(vs, _texts(vs), ask=ask, criteria=CRITERIA, store=store)

    assert [len(call) for call in ask.calls] == [10, 10, 3]


def test_failed_batch_is_retried_once_then_left_unscored(store):
    vs = vacancies(1, 2, 3)
    ask = Ask(error="boom")
    scores, warnings = score_vacancies(vs, _texts(vs), ask=ask, criteria=CRITERIA, store=store)

    assert len(ask.calls) == 2
    assert all(not s.scored for s in scores.values())
    assert all("boom" in s.why for s in scores.values())
    assert len(warnings) == 1
    assert "не оценено" in warnings[0] and "boom" in warnings[0]


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


def test_missing_id_in_answer_is_unscored_not_lost(store):
    vs = vacancies(1, 2, 3)
    scores, warnings = score_vacancies(vs, _texts(vs), ask=Ask(skip={"hh:2"}), criteria=CRITERIA, store=store)

    assert set(scores) == {"hh:1", "hh:2", "hh:3"}
    assert not scores["hh:2"].scored
    assert scores["hh:1"].value == 7
    assert len(warnings) == 1


def test_card_scores_are_marked_and_not_cached(store):
    with_text, bare = vacancies(1, 2)
    scores, _ = score_vacancies(
        [with_text, bare], _texts([with_text]), ask=Ask(), criteria=CRITERIA, store=store,
    )
    digest = criteria_hash(CRITERIA)

    assert scores["hh:2"].from_card is True
    assert scores["hh:1"].from_card is False
    assert store.get_score("hh:2", digest) is None
    assert store.get_score("hh:1", digest) == scores["hh:1"]


def test_cached_scores_skip_claude(store):
    vs = vacancies(1, 2)
    score_vacancies(vs, _texts(vs), ask=Ask(), criteria=CRITERIA, store=store)
    again = Ask()
    scores, warnings = score_vacancies(vs, _texts(vs), ask=again, criteria=CRITERIA, store=store)

    assert again.calls == []
    assert scores["hh:1"].value == 7
    assert warnings == []


def test_changed_criteria_rescore_from_cached_text_without_hh(store):
    vs = vacancies(1, 2)
    urls = []

    def fetch_page(url):
        urls.append(url)
        return vacancy_page(int(url.rsplit("/", 1)[1]))

    score_pass(vs, fetch_page=fetch_page, store=store, pause=lambda s: None, ask=Ask(), criteria=CRITERIA)
    assert len(urls) == 2

    edited = Criteria(CRITERIA.profile + " + переработки")
    ask = Ask(score=3)
    scores, _ = score_pass(vs, fetch_page=fetch_page, store=store, pause=lambda s: None, ask=ask, criteria=edited)

    assert len(urls) == 2, "тексты должны браться из кеша"
    assert len(ask.calls) == 1
    assert scores["hh:1"].value == 3


def test_failed_batch_stops_the_remaining_batches(store):
    vs = vacancies(*range(25))
    ask = Ask(error="claude не ответил за 600 с")
    scores, warnings = score_vacancies(vs, _texts(vs), ask=ask, criteria=CRITERIA, store=store)

    assert len(ask.calls) == 2, "одна пачка с повтором, остальные не отправляются"
    assert len(scores) == 25
    assert all(not s.scored and "600 с" in s.why for s in scores.values())
    assert len(warnings) == 1


def test_ask_claude_wraps_other_os_errors():
    with pytest.raises(ScoringError):
        ask_claude("s", "[]", model="sonnet", run=_raise(PermissionError("claude")))


def test_criteria_hash_depends_on_the_model():
    opus = Criteria(CRITERIA.profile, model="opus")
    assert criteria_hash(opus) != criteria_hash(CRITERIA)
