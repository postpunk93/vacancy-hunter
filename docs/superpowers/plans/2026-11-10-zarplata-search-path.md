# План: вернуть zarplata в выдачу

Основание: `docs/superpowers/specs/2026-11-10-zarplata-search-path-design.md`.

## Шаги

1. `hunter/sources.py`: у zarplata `search_path = "/search/vacancy"`.
2. `tests/fixtures/zarplata_search_python.html`: заменить страницей с
   `/search/vacancy?text=python&area=113&order_by=publication_time&search_period=7&items_on_page=50`.
3. `tests/test_sources.py`: ожидать `/search/vacancy`; переименовать тест так,
   чтобы он говорил про адрес выдачи.
4. `tests/test_parse.py`: тест «нет вилки» — взять из новой фикстуры карточку
   без компенсации; добавить проверку «карточек больше одной».
5. Прогнать `uv run pytest`.
6. Живая проверка: `hunter --days 1 --dry-run --no-score`, убедиться, что
   zarplata отдаёт карточки по каждому запросу и не пишет ошибок разбора.
7. Ревью ветки субагентом `reviewer`, затем коммит.

## Готово, когда

- Тесты зелёные.
- В живом проходе zarplata даёт десятки карточек на запрос вместо 0–2.
- Остальные источники и оценка не изменились.
