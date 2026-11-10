from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..fetch import Blocked, FetchError, NotFound
from ..models import SearchQuery
from ..parse import ParseError
from ..sources import build_url
from ..text import plural
from .extract import VACANCY_URL, Captcha, card_row, parse_detail, search_cards
from .store import Corpus

FetchPage = Callable[[str], str]

ITEMS_PER_PAGE = 50  # совпадает с items_on_page в sources.build_url
MANUAL_QUERY = "manual"

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_BLOCKED = 3

_CARD_ERRORS = (KeyError, TypeError, ValueError, AttributeError)


def _no_wait() -> None:
    pass


@dataclass
class CollectReport:
    role: str
    cards: int = 0
    new: int = 0
    pages: int = 0
    error: str | None = None
    blocked: str | None = None

    @property
    def existing(self) -> int:
        return self.cards - self.new

    @property
    def exit_code(self) -> int:
        if self.blocked:
            return EXIT_BLOCKED
        return EXIT_PARTIAL if self.error else EXIT_OK

    def summary(self) -> str:
        lines = [
            f"{self.role}: {plural(self.cards, 'карточка', 'карточки', 'карточек')}, "
            f"новых {self.new}, уже были {self.existing} "
            f"(страниц выдачи: {self.pages})"
        ]
        if self.blocked:
            lines.append(f"ОСТАНОВЛЕНО — блокировка: {self.blocked}")
        if self.error:
            lines.append(f"ОШИБКА: {self.error}")
        return "\n".join(lines)


def collect(corpus: Corpus, fetch_page: FetchPage, query: SearchQuery, role: str,
            wait: Callable[[], None] = _no_wait) -> CollectReport:
    report = CollectReport(role)
    counted: set[int] = set()
    for page in range(query.max_pages):
        if page:
            wait()
        url = build_url("hh", query, page)
        try:
            cards = search_cards(fetch_page(url))
            # Сначала разбираем всю страницу, потом пишем: битая карточка
            # не оставит полстраницы в базе.
            rows = [card_row(raw) for raw in cards]
        except (Blocked, Captcha) as exc:
            report.blocked = str(exc)
            break
        except FetchError as exc:
            report.error = str(exc)
            break
        except ParseError as exc:
            report.error = f"{url}: {exc}"
            break
        except _CARD_ERRORS as exc:
            report.error = (
                f"{url}: карточка не разобралась "
                f"({exc.__class__.__name__}: {exc}) — сайт сменил формат"
            )
            break

        for row in rows:
            is_new, vacancy_id = corpus.upsert_card(row)
            corpus.add_hit(vacancy_id, role, query.text)
            # Рекламная карточка повторяет обычную — в отчёте одна вакансия.
            if vacancy_id not in counted:
                counted.add(vacancy_id)
                report.cards += 1
                report.new += is_new
        report.pages += 1
        if len(cards) < ITEMS_PER_PAGE:
            break
    return report


@dataclass
class FetchReport:
    fetched: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    deleted: list[int] = field(default_factory=list)
    failed: list[tuple[int, str]] = field(default_factory=list)
    not_reached: list[int] = field(default_factory=list)
    blocked: str | None = None

    @property
    def exit_code(self) -> int:
        if self.blocked:
            return EXIT_BLOCKED
        return EXIT_PARTIAL if self.failed else EXIT_OK

    def summary(self) -> str:
        lines = [
            f"скачано {len(self.fetched)}, уже были {len(self.skipped)}, "
            f"удалены на hh {len(self.deleted)}, ошибок {len(self.failed)}"
        ]
        lines += [f"  {vacancy_id}: {error}" for vacancy_id, error in self.failed]
        if self.blocked:
            lines.append(f"ОСТАНОВЛЕНО — блокировка: {self.blocked}")
            lines.append("не обработаны: " + " ".join(map(str, self.not_reached)))
        return "\n".join(lines)


def fetch_details(corpus: Corpus, fetch_page: FetchPage, ids: list[int], *,
                  role: str | None = None, refresh: bool = False,
                  wait: Callable[[], None] = _no_wait) -> FetchReport:
    report = FetchReport()
    requested = False
    for index, vacancy_id in enumerate(ids):
        if not refresh and corpus.has_detail(vacancy_id):
            report.skipped.append(vacancy_id)
            if role and corpus.exists(vacancy_id):
                corpus.add_hit(vacancy_id, role, MANUAL_QUERY)
            continue

        if requested:
            wait()
        requested = True
        try:
            detail = parse_detail(fetch_page(VACANCY_URL.format(id=vacancy_id)))
        except (Blocked, Captcha) as exc:
            report.blocked = str(exc)
            report.not_reached = list(ids[index:])
            break
        except NotFound:
            corpus.mark_deleted(vacancy_id)
            report.deleted.append(vacancy_id)
            continue
        except (FetchError, ParseError) as exc:
            report.failed.append((vacancy_id, str(exc)))
            continue
        except _CARD_ERRORS as exc:
            # Одна странная страница не должна ронять докачку остальных.
            report.failed.append((
                vacancy_id,
                f"страница не разобралась ({exc.__class__.__name__}: {exc}) — сайт сменил формат",
            ))
            continue

        corpus.save_detail(detail)
        if role:
            corpus.add_hit(detail.vacancy_id, role, MANUAL_QUERY)
        report.fetched.append(vacancy_id)
    return report
