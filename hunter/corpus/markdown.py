from __future__ import annotations

import re
from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr"}
_BOLD_TAGS = {"strong", "b"}
_ITALIC_TAGS = {"em", "i"}
_CELL_TAGS = {"td", "th"}


class _Converter(HTMLParser):
    """Описания вакансий на hh — простая разметка редактора: абзацы,
    списки, жирный. Всё прочее превращаем в текст, не теряя слов."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.lists: list[list] = []  # [тег, номер последнего пункта]
        self.line_start = True

    def _newline(self, text: str) -> None:
        self.parts.append(text)
        self.line_start = True

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS and self.lists:
            # Абзац внутри пункта (<li><p>…) — часть пункта: сразу после
            # маркера ничего не добавляем, следующий абзац — с отступом.
            if not self.line_start:
                self._newline("\n" + "  " * len(self.lists))
        elif tag in _BLOCK_TAGS:
            self._newline("\n\n")
        elif tag == "br":
            self._newline("\n")
        elif tag in ("ul", "ol"):
            # Вложенный список начинается с пункта — пункт сам даёт перевод
            # строки, лишняя пустая строка разорвала бы родительский пункт.
            if not self.lists:
                self._newline("\n\n")
            self.lists.append([tag, 0])
        elif tag == "li":
            indent = "  " * max(len(self.lists) - 1, 0)
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                marker = f"{self.lists[-1][1]}. "
            else:
                marker = "- "
            self._newline(f"\n{indent}{marker}")
        elif tag in _BOLD_TAGS:
            self.parts.append("**")
        elif tag in _ITALIC_TAGS:
            self.parts.append("*")
        elif tag in _CELL_TAGS and not self.line_start:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS and self.lists:
            pass  # следующий пункт сам начнётся с новой строки
        elif tag in _BLOCK_TAGS:
            self._newline("\n\n")
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            if not self.lists:
                self._newline("\n\n")
        elif tag in _BOLD_TAGS:
            self.parts.append("**")
        elif tag in _ITALIC_TAGS:
            self.parts.append("*")

    def handle_data(self, data):
        text = re.sub(r"\s+", " ", data)
        if self.line_start:
            text = text.lstrip()
        if text:
            self.parts.append(text)
            self.line_start = False


def html_to_markdown(html: str) -> str:
    converter = _Converter()
    converter.feed(html)
    converter.close()
    lines = [line.rstrip() for line in "".join(converter.parts).split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
