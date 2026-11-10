from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

from ..fetch import Blocked, FetchError, NotFound
from ..models import SearchQuery
from ..parse import ParseError
from ..platforms import Captcha, format_ref, get
from ..text import plural
from .store import Corpus

FetchPage = Callable[[str], str]

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
    source: str = "hh"
    cards: int = 0
    new: int = 0
    pages: int = 0
    error: str | None = None
    blocked: str | None = None
    # Почему запрос не пошёл на площадку (нет формулировки под неё).
    skipped: str | None = None
    # Что площадка проигнорировала или за сколько дней смотрела.
    notes: list[str] = field(default_factory=list)

    @property
    def existing(self) -> int:
        return self.cards - self.new

    @property
    def exit_code(self) -> int:
        if self.blocked:
            return EXIT_BLOCKED
        return EXIT_PARTIAL if self.error else EXIT_OK

    def summary(self) -> str:
        if self.skipped:
            return f"{self.role} ({self.source}): {self.skipped}"
        lines = [
            f"{self.role} ({self.source}): "
            f"{plural(self.cards, 'карточка', 'карточки', 'карточек')}, "
            f"новых {self.new}, уже были {self.existing} "
            f"(страниц выдачи: {self.pages})"
        ]
        lines += self.notes
        if self.blocked:
            lines.append(f"ОСТАНОВЛЕНО — блокировка: {self.blocked}")
        if self.error:
            lines.append(f"ОШИБКА: {self.error}")
        return "\n".join(lines)


def collect(corpus: Corpus, fetch_page: FetchPage, query: SearchQuery, role: str,
            wait: Callable[[], None] = _no_wait, *, source: str = "hh") -> CollectReport:
    report = CollectReport(role, source)
    platform = get(source)
    text = query.text_for(source)
    if text is None:
        report.skipped = f"не отправлен: нет формулировки запроса под {source}"
        return report
    query = replace(query, text=text)
    ignored = platform.unsupported(query)
    if ignored:
        report.notes.append(
            f"{source}: не поддерживает {', '.join(ignored)} — проигнорировано"
        )
    supported = platform.search_period(query.search_period)
    if supported < query.search_period:
        report.notes.append(
            f"{source}: смотрели за {supported} дн. — больше площадка не отдаёт"
        )
    counted: set[int] = set()
    seen_external: set[str] = set()
    for page in range(min(query.max_pages, platform.max_pages)):
        if page:
            wait()
        url = platform.search_url(query, page)
        try:
            cards = platform.parse_cards(fetch_page(url))
            # Сначала разбираем всю страницу, потом пишем: битая карточка
            # не оставит полстраницы в базе.
            rows = [card.row for card in cards]
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
        # Страница без единой новой карточки значит, что выдача кончилась
        # или площадка добила её «похожими»: листать дальше незачем.
        fresh = {card.external_id for card in cards} - seen_external
        seen_external |= {card.external_id for card in cards}
        if not fresh or len(cards) < platform.page_size:
            break
    return report


@dataclass
class FetchReport:
    """Отчёт докачки. Ссылки в списках — в форме `parse_ref`/`format_ref`:
    у hh это голый id, у остальных площадок `площадка:id`."""

    fetched: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    not_reached: list[str] = field(default_factory=list)
    blocked: str | None = None

    @property
    def exit_code(self) -> int:
        if self.blocked:
            return EXIT_BLOCKED
        return EXIT_PARTIAL if self.failed else EXIT_OK

    def summary(self) -> str:
        lines = [
            f"скачано {len(self.fetched)}, уже были {len(self.skipped)}, "
            f"удалены {len(self.deleted)}, ошибок {len(self.failed)}"
        ]
        lines += [f"  {ref}: {error}" for ref, error in self.failed]
        if self.blocked:
            lines.append(f"ОСТАНОВЛЕНО — блокировка: {self.blocked}")
            lines.append("не обработаны: " + " ".join(self.not_reached))
        return "\n".join(lines)


def fetch_details(corpus: Corpus, fetch_page: FetchPage, refs: list[tuple[str, str]], *,
                  role: str | None = None, refresh: bool = False,
                  wait: Callable[[], None] = _no_wait) -> FetchReport:
    """Скачивает страницы вакансий по парам (площадка, внешний id).
    Вакансию, которой нет в базе, добавляет: это единственный путь
    завести её по ссылке."""
    report = FetchReport()
    requested = False
    for index, (source, external_id) in enumerate(refs):
        ref = format_ref(source, external_id)
        vacancy_id = corpus.find(source, external_id)
        if not refresh and vacancy_id is not None and corpus.has_detail(vacancy_id):
            report.skipped.append(ref)
            if role:
                corpus.add_hit(vacancy_id, role, MANUAL_QUERY)
            continue

        if requested:
            wait()
        requested = True
        platform = get(source)
        try:
            detail = platform.parse_detail(fetch_page(platform.vacancy_url(external_id)))
        except (Blocked, Captcha) as exc:
            report.blocked = str(exc)
            report.not_reached = [format_ref(s, e) for s, e in refs[index:]]
            break
        except NotFound:
            if vacancy_id is not None:
                corpus.mark_deleted(vacancy_id)
            report.deleted.append(ref)
            continue
        except (FetchError, ParseError) as exc:
            report.failed.append((ref, str(exc)))
            continue
        except _CARD_ERRORS as exc:
            # Одна странная страница не должна ронять докачку остальных.
            report.failed.append((
                ref,
                f"страница не разобралась ({exc.__class__.__name__}: {exc}) — сайт сменил формат",
            ))
            continue

        corpus.save_detail(detail)
        if role:
            corpus.add_hit(corpus.find(detail.source, detail.external_id), role, MANUAL_QUERY)
        report.fetched.append(ref)
    return report
