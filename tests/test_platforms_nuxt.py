import pytest

from hunter.parse import ParseError
from hunter.platforms.nuxt import parse_nuxt


def page(body: str, args: str = "{}", params: str = "a") -> str:
    """Страница Nuxt 2 в function-режиме, как её отдаёт rabota.ru."""
    return (
        "<html><body><script>window.__NUXT__="
        f"(function({params}){{{body}}}({args}));"
        "</script></body></html>"
    )


def test_arguments_are_substituted_for_parameters_by_position():
    html = page(
        "return {first:first,second:second,third:third,"
        "yes:!0,no:!1,none:void 0,negative:-1.5,real:2.25};",
        params="first,second,third",
        args='{"x":1},[2,3],"строка"',
    )
    assert parse_nuxt(html) == {
        "first": {"x": 1},
        "second": [2, 3],
        "third": "строка",
        "yes": True,
        "no": False,
        "none": None,
        "negative": -1.5,
        "real": 2.25,
    }


def test_return_inside_a_string_does_not_end_the_body():
    html = page(r'return {t:"напишите return {x:1} в отзыве",k:"после"};')
    assert parse_nuxt(html) == {"t": "напишите return {x:1} в отзыве", "k": "после"}


def test_only_the_return_that_closes_the_body_is_taken():
    html = page("if(a){return {early:!0}}return {late:!0};")
    assert parse_nuxt(html) == {"late": True}


def test_unicode_escapes_in_strings_are_decoded():
    # r-строка: до парсера доходит буквально \u0440, а не буква «р».
    html = page(r'return {t:"\u0440\u0435\u0434\u0430\u043a\u0442\u043e\u0440"};')
    assert parse_nuxt(html) == {"t": "редактор"}


def test_keys_may_be_unquoted_and_strings_single_quoted():
    html = page("return {price:'100','quoted-key':\"v\"};")
    assert parse_nuxt(html) == {"price": "100", "quoted-key": "v"}


def test_page_without_payload_raises_parse_error():
    with pytest.raises(ParseError, match="window.__NUXT__"):
        parse_nuxt("<html>капча</html>")


def test_broken_payload_reports_position():
    with pytest.raises(ParseError, match="позици"):
        parse_nuxt(page("return {a: };"))
