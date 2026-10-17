import pytest

from hunter.corpus.extract import card_row, parse_detail
from hunter.corpus.show import render_table, render_vacancy
from hunter.corpus.store import Corpus
from tests.factories import search_card, vacancy_page


@pytest.fixture
def corpus(tmp_path):
    with Corpus.open(tmp_path / "v.db", create=True) as opened:
        yield opened


def rendered(corpus, vacancy_id):
    return render_vacancy(corpus.vacancy(vacancy_id), corpus.hits(vacancy_id), corpus.verdict(vacancy_id))


def test_full_vacancy(corpus):
    corpus.upsert_card(card_row(search_card(1, name="Python backend")))
    corpus.save_detail(parse_detail(vacancy_page(1)))
    corpus.add_hit(1, "backend", "python AND backend")

    text = rendered(corpus, 1)

    assert text.startswith("## Python backend — Компания")
    assert "https://hh.ru/vacancy/1" in text
    assert "от 300 000 RUR, на руки, выплаты MONTHLY" in text
    assert "формат: REMOTE" in text
    assert "Откликов: 5" in text
    assert "Роли: backend (python AND backend)" in text
    assert "Вердикт: нет" in text
    assert "Навыки: Python, SQL" in text
    assert text.endswith("Пишем на **Python**")
    assert "Сигналы" not in text


def test_quality_signals(corpus):
    raw = search_card(1, company={"id": 2, "visibleName": "Иван", "@trusted": False,
                                  "@category": "PRIVATE_INDIVIDUAL"},
                      vacancyProperties={"properties": [{"property": [{
                          "propertyType": "HH_ANTIFRAUD_SUSPICIOUS_VACANCY",
                          "parameters": [{"parameter": [{"key": "antifraudPessimizePower", "value": "0.8"}]}],
                      }]}]})
    corpus.upsert_card(card_row(raw))

    assert "Сигналы: работодатель не проверен hh; частное лицо; антифрод hh 0.80" in rendered(corpus, 1)


def test_without_text_hints_fetch(corpus):
    corpus.upsert_card(card_row(search_card(3, compensation={"noCompensation": {}})))

    text = rendered(corpus, 3)

    assert "Зарплата: не указана" in text
    assert "vacancies fetch 3" in text


def test_deleted_vacancy(corpus):
    corpus.upsert_card(card_row(search_card(3)))
    corpus.mark_deleted(3)

    text = rendered(corpus, 3)

    assert "удалена" in text
    assert "vacancies fetch" not in text


def test_verdict_with_note(corpus):
    corpus.upsert_card(card_row(search_card(1)))
    corpus.set_verdict(1, "maybe", "стек близко, домен скучный")

    assert "Вердикт: maybe — стек близко, домен скучный" in rendered(corpus, 1)


def test_table_truncates_and_escapes():
    table = render_table(["id", "text"], [(1, "а" * 100), (2, None), (3, "x|y\nz")], max_width=10)

    lines = table.split("\n")
    assert lines[0] == "| id | text |"
    assert lines[2] == "| 1 | ааааааааа… |"
    assert lines[3] == "| 2 |  |"
    assert lines[4] == "| 3 | x\\|y z |"
    assert lines[-1] == "(3 строки)"


def test_table_with_no_rows():
    assert render_table(["id"], []).endswith("(0 строк)")


def test_statement_without_result_columns():
    assert render_table([], []) == "(запрос ничего не вернул)"
