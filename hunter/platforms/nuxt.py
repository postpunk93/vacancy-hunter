"""Разбор payload `window.__NUXT__` у Nuxt 2 в function-режиме.

Payload — не JSON: `(function(a,b,…){ …; return {…} }(аргументы));`, тело
ссылается на параметры по имени. Значения разбираются рекурсивным спуском,
а `return` ищется с конца: слово встречается и в тексте вакансий, поэтому
годится только тот, после которого тело закрывается.

Модуль только разбирает: что считать капчей и как звать площадку, решает
сама площадка.
"""

from __future__ import annotations

from ..parse import ParseError

DEFAULT_MARKER = "window.__NUXT__="

# Хвост тела после значения: точка с запятой и закрывающая скобка функции.
_TAIL = " \t\r\n;}"


class _Parser:
    """Рекурсивный спуск по литералу: объекты, массивы, строки, числа, флаги."""

    def __init__(self, text: str, pos: int = 0,
                 params: dict[str, object] | None = None) -> None:
        self.text = text
        self.pos = pos
        self.params = params or {}

    def error(self, message: str) -> ParseError:
        snippet = self.text[self.pos:self.pos + 40]
        return ParseError(f"{message} на позиции {self.pos}: {snippet!r}")

    def skip(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in " \t\r\n,":
            self.pos += 1

    def at_end(self) -> bool:
        return self.pos >= len(self.text)

    def skip_tail(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in _TAIL:
            self.pos += 1

    def value(self) -> object:
        self.skip()
        if self.at_end():
            raise self.error("значение не дочитано")
        char = self.text[self.pos]
        if char == "{":
            return self.object()
        if char == "[":
            return self.array()
        if char in "'\"":
            return self.string()
        if self.text.startswith("void 0", self.pos):
            self.pos += len("void 0")
            return None
        if self.text.startswith("!0", self.pos):
            self.pos += 2
            return True
        if self.text.startswith("!1", self.pos):
            self.pos += 2
            return False
        if self.text.startswith("true", self.pos):
            self.pos += 4
            return True
        if self.text.startswith("false", self.pos):
            self.pos += 5
            return False
        if self.text.startswith("null", self.pos):
            self.pos += 4
            return None
        if char.isdigit() or char in "+-.":
            return self.number()
        if char.isalpha() or char in "_$":
            return self.params.get(self.ident())
        raise self.error(f"непонятный символ {char!r}")

    def ident(self) -> str:
        start = self.pos
        while self.pos < len(self.text) and (
            self.text[self.pos].isalnum() or self.text[self.pos] in "_$"
        ):
            self.pos += 1
        return self.text[start:self.pos]

    def string(self) -> str:
        quote = self.text[self.pos]
        self.pos += 1
        buffer: list[str] = []
        escape = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}
        while True:
            if self.at_end():
                raise self.error("строка не закрыта")
            char = self.text[self.pos]
            if char == "\\":
                self.pos += 1
                if self.at_end():
                    raise self.error("строка не закрыта")
                marker = self.text[self.pos]
                if marker == "u":
                    digits = self.text[self.pos + 1:self.pos + 5]
                    try:
                        buffer.append(chr(int(digits, 16)))
                    except ValueError:
                        raise self.error("\\u без четырёх hex-цифр") from None
                    self.pos += 5
                    continue
                buffer.append(escape.get(marker, marker))
                self.pos += 1
                continue
            if char == quote:
                self.pos += 1
                return "".join(buffer)
            buffer.append(char)
            self.pos += 1

    def number(self) -> int | float:
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] in "+-.eE0123456789":
            self.pos += 1
        raw = self.text[start:self.pos]
        try:
            return float(raw) if any(ch in raw for ch in ".eE") else int(raw)
        except ValueError:
            raise self.error(f"число {raw!r} не разбирается") from None

    def object(self) -> dict:
        self.pos += 1
        result: dict[str, object] = {}
        while True:
            self.skip()
            if self.at_end():
                raise self.error("объект не закрыт")
            if self.text[self.pos] == "}":
                self.pos += 1
                return result
            if self.text[self.pos] in "'\"":
                key = self.string()
            else:
                key = self.ident()
            self.skip()
            if self.at_end() or self.text[self.pos] != ":":
                raise self.error("после ключа объекта нет двоеточия")
            self.pos += 1
            result[key] = self.value()

    def array(self) -> list:
        self.pos += 1
        result: list[object] = []
        while True:
            self.skip()
            if self.at_end():
                raise self.error("массив не закрыт")
            if self.text[self.pos] == "]":
                self.pos += 1
                return result
            result.append(self.value())


def _arguments(text: str, params: list[str]) -> dict[str, object]:
    """Аргументы вызова по позиции — значениям параметров."""
    parser = _Parser(text)
    values: list[object] = []
    while True:
        parser.skip()
        if parser.at_end():
            break
        values.append(parser.value())
    return dict(zip(params, values))


def _returned(body: str, params: dict[str, object], marker: str) -> object:
    """Значение последнего `return`, после которого тело закрывается."""
    search_end = len(body)
    last_error: ParseError | None = None
    while True:
        start = body.rfind("return ", 0, search_end)
        if start < 0:
            if last_error is not None:
                raise last_error
            raise ParseError(f"{marker} не разобрался: ни один return не закрывает тело")
        parser = _Parser(body, start + len("return "), params)
        try:
            value = parser.value()
        except ParseError as exc:
            last_error = exc
            search_end = start
            continue
        parser.skip_tail()
        if parser.at_end():
            return value
        last_error = parser.error("тело после return не кончается")
        search_end = start


def parse_nuxt(page_html: str, marker: str = DEFAULT_MARKER) -> dict:
    """Состояние Nuxt-страницы. Нет маркера, заголовка или значения — ParseError."""
    index = page_html.find(marker)
    if index < 0:
        raise ParseError(f"в странице нет {marker}")
    source = page_html[index + len(marker):]
    cut = source.find("</script>")
    if cut >= 0:
        source = source[:cut]

    source = source.strip()
    if source.endswith(";"):
        source = source[:-1].rstrip()
    if not source.startswith("(function(") or not source.endswith("))"):
        raise ParseError(
            f"{marker} — не вызов Nuxt-функции: {source[:40]!r}"
        )

    params_start = len("(function(")
    params_end = source.find("){", params_start)
    if params_end < 0:
        raise ParseError(f"{marker} не разобрался: у функции нет тела")
    params = [name.strip() for name in source[params_start:params_end].split(",")
              if name.strip()]
    body_start = params_end + 2

    call = source[:-2]
    split = call.rfind("}(")
    if split < 0:
        raise ParseError(f"{marker} не разобрался: у функции нет аргументов")
    body = call[body_start:split + 1]
    values = _arguments(call[split + 2:], params)

    state = _returned(body, values, marker)
    if not isinstance(state, dict):
        raise ParseError(f"{marker} разобрался не в словарь — сайт сменил формат")
    return state
