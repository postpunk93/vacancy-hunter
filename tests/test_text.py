from hunter.text import plural


def test_plural_is_a_reusable_general_helper():
    assert plural(1, "запросом", "запросами", "запросами") == "1 запросом"
    assert plural(2, "запросом", "запросами", "запросами") == "2 запросами"
    assert plural(12, "запросом", "запросами", "запросами") == "12 запросами"
