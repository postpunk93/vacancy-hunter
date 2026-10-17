from hunter.corpus.markdown import html_to_markdown


def test_paragraphs_become_blank_line_separated():
    assert html_to_markdown("<p>Первый</p><p>Второй</p>") == "Первый\n\nВторой"


def test_bold_heading_and_bullet_list():
    html = "<p><strong>Требования:</strong></p><ul><li>Python</li><li>SQL</li></ul>"
    assert html_to_markdown(html) == "**Требования:**\n\n- Python\n- SQL"


def test_ordered_list_is_numbered():
    assert html_to_markdown("<ol><li>a</li><li>b</li></ol>") == "1. a\n2. b"


def test_nested_list_is_indented_without_blank_lines():
    html = "<ul><li>a<ul><li>b</li></ul></li><li>c</li></ul>"
    assert html_to_markdown(html) == "- a\n  - b\n- c"


def test_entities_and_line_breaks():
    assert html_to_markdown("Опыт&nbsp;от 3 лет<br>C&amp;C++") == "Опыт от 3 лет\nC&C++"


def test_whitespace_is_collapsed_and_lines_are_not_indented():
    assert html_to_markdown("<p>\n   много   пробелов \n</p>") == "много пробелов"


def test_unknown_markup_keeps_its_text_readable():
    html = "<table><tr><td>Зарплата</td><td>300к</td></tr></table><p><em>офис</em></p>"
    assert html_to_markdown(html) == "Зарплата 300к\n\n*офис*"


def test_list_after_paragraph_has_single_blank_line():
    assert html_to_markdown("<p>Задачи</p><ul><li>писать код</li></ul>") == "Задачи\n\n- писать код"


def test_list_items_wrapped_in_paragraphs():
    html = "<ul><li><p>Пункт</p></li><li><p>Второй</p></li></ul>"
    assert html_to_markdown(html) == "- Пункт\n- Второй"


def test_second_paragraph_inside_item_stays_in_the_item():
    assert html_to_markdown("<ul><li><p>a</p><p>b</p></li></ul>") == "- a\n  b"


def test_empty_input():
    assert html_to_markdown("") == ""
