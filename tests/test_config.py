import pytest

from hunter.config import ConfigError, load_config, load_secrets, with_search_period


def write(tmp_path, text, name="config.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_merge_into_each_query(tmp_path):
    path = write(tmp_path, """
sources: [hh, zarplata]
defaults:
  area: 113
  salary: 200000
queries:
  - name: "Python backend"
    text: "python"
  - name: "Data"
    text: "data engineer"
    area: 1
""")
    cfg = load_config(path)

    assert cfg.sources == ("hh", "zarplata")
    assert len(cfg.queries) == 2
    assert cfg.queries[0].area == 113
    assert cfg.queries[0].salary == 200000
    assert cfg.queries[1].area == 1, "запрос должен перебивать defaults"
    assert cfg.queries[1].salary == 200000, "остальные defaults остаются"


def test_missing_queries_is_an_error(tmp_path):
    path = write(tmp_path, "sources: [hh]\n")
    with pytest.raises(ConfigError, match="ни одного запроса"):
        load_config(path)


def test_unknown_source_is_rejected_with_the_list_of_valid_ones(tmp_path):
    path = write(tmp_path, """
sources: [hh, linkedin]
queries:
  - name: "x"
    text: "y"
""")
    with pytest.raises(ConfigError, match="linkedin"):
        load_config(path)


def test_typo_in_a_query_field_is_caught(tmp_path):
    path = write(tmp_path, """
queries:
  - name: "x"
    text: "y"
    experiense: between1And3
""")
    with pytest.raises(ConfigError, match="experiense"):
        load_config(path)


def test_query_without_text_is_rejected(tmp_path):
    path = write(tmp_path, """
queries:
  - name: "x"
""")
    with pytest.raises(ConfigError, match="text"):
        load_config(path)


def test_missing_file_gives_a_readable_error(tmp_path):
    with pytest.raises(ConfigError, match="не найден"):
        load_config(tmp_path / "nope.yaml")


def test_secrets_are_read_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT_ID", raising=False)
    path = write(tmp_path, '# comment\nTG_BOT_TOKEN="123:abc"\nTG_CHAT_ID=456\n', name=".env")

    token, chat_id = load_secrets(path)

    assert token == "123:abc"
    assert chat_id == "456"


def test_environment_wins_over_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_BOT_TOKEN", "from-env")
    monkeypatch.setenv("TG_CHAT_ID", "999")
    path = write(tmp_path, "TG_BOT_TOKEN=from-file\nTG_CHAT_ID=1\n", name=".env")

    assert load_secrets(path) == ("from-env", "999")


def test_missing_secrets_are_reported(tmp_path, monkeypatch):
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_CHAT_ID", raising=False)
    with pytest.raises(ConfigError, match="TG_BOT_TOKEN"):
        load_secrets(tmp_path / ".env")


def test_experience_as_a_single_string_becomes_a_one_item_tuple(tmp_path):
    path = write(tmp_path, """
queries:
  - name: "x"
    text: "y"
    experience: between1And3
""")
    cfg = load_config(path)
    assert cfg.queries[0].experience == ("between1And3",)


def test_experience_as_a_list_becomes_a_tuple(tmp_path):
    path = write(tmp_path, """
queries:
  - name: "x"
    text: "y"
    experience: [noExperience, between1And3]
""")
    cfg = load_config(path)
    assert cfg.queries[0].experience == ("noExperience", "between1And3")


def test_experience_defaults_to_an_empty_tuple_when_not_set(tmp_path):
    path = write(tmp_path, """
queries:
  - name: "x"
    text: "y"
""")
    cfg = load_config(path)
    assert cfg.queries[0].experience == ()


def test_experience_as_a_scalar_int_is_rejected(tmp_path):
    path = write(tmp_path, """
queries:
  - name: "x"
    text: "y"
    experience: 1
""")
    with pytest.raises(ConfigError, match="experience"):
        load_config(path)


def test_experience_as_a_mapping_is_rejected(tmp_path):
    path = write(tmp_path, """
queries:
  - name: "x"
    text: "y"
    experience: {noExperience: yes}
""")
    with pytest.raises(ConfigError, match="experience"):
        load_config(path)


@pytest.mark.parametrize("value", [0, -1, 31, "неделя"])
def test_search_period_outside_one_to_thirty_days_is_rejected(tmp_path, value):
    path = write(tmp_path, f"""
queries:
  - name: q
    text: python
    search_period: {value}
""")
    with pytest.raises(ConfigError, match="search_period"):
        load_config(path)


def test_period_override_replaces_search_period_in_every_query(tmp_path):
    path = write(tmp_path, """
defaults:
  search_period: 2
queries:
  - name: a
    text: python
  - name: b
    text: go
    search_period: 1
""")
    cfg = with_search_period(load_config(path), 7)

    assert [q.search_period for q in cfg.queries] == [7, 7]


_QUERIES = """
queries:
  - name: "Q"
    text: "q"
"""


def _profile(tmp_path):
    return write(tmp_path, "профиль", name="profile.md")


def test_scoring_section_is_optional(tmp_path):
    assert load_config(write(tmp_path, _QUERIES)).scoring is None


def test_scoring_disabled_means_none(tmp_path):
    path = write(tmp_path, _QUERIES + """
scoring:
  enabled: false
  profile: /nonexistent/profile.md
""")
    assert load_config(path).scoring is None


def test_profile_path_is_relative_to_config(tmp_path):
    me = tmp_path / "me"
    me.mkdir()
    profile = write(me, "профиль", name="profile.md")
    path = write(me, _QUERIES + "\nscoring:\n  profile: profile.md\n")

    assert load_config(path).scoring.profile == profile


def test_profile_defaults_to_profile_md(tmp_path):
    profile = _profile(tmp_path)
    path = write(tmp_path, _QUERIES + "\nscoring:\n  enabled: true\n")

    assert load_config(path).scoring.profile == profile


def test_profile_path_expands_home_and_must_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _profile(tmp_path)
    path = write(tmp_path, _QUERIES + "\nscoring:\n  profile: ~/profile.md\n")
    assert load_config(path).scoring.profile == tmp_path / "profile.md"

    missing = write(tmp_path, _QUERIES + "\nscoring:\n  profile: ~/nope.md\n")
    with pytest.raises(ConfigError, match="nope.md"):
        load_config(missing)


@pytest.mark.parametrize("old", ["portrait", "conflict"])
def test_old_portrait_and_conflict_keys_explain_the_change(tmp_path, old):
    _profile(tmp_path)
    path = write(tmp_path, _QUERIES + f"\nscoring:\n  {old}: x.md\n")

    with pytest.raises(ConfigError, match="устарело.*profile.md"):
        load_config(path)


def test_scoring_defaults(tmp_path):
    _profile(tmp_path)
    path = write(tmp_path, _QUERIES + "\nscoring:\n  profile: profile.md\n")
    scoring = load_config(path).scoring
    assert scoring.threshold == 5
    assert scoring.backend == "opencode"
    assert scoring.model == "opencode/big-pickle"


def test_scoring_claude_backend_defaults_to_sonnet(tmp_path):
    _profile(tmp_path)
    path = write(tmp_path, _QUERIES + """
scoring:
  profile: profile.md
  backend: claude
""")
    assert load_config(path).scoring.model == "sonnet"


def test_scoring_model_overrides_the_backend_default(tmp_path):
    _profile(tmp_path)
    path = write(tmp_path, _QUERIES + """
scoring:
  profile: profile.md
  backend: claude
  model: opus
""")
    assert load_config(path).scoring.model == "opus"


def test_scoring_unknown_backend_rejected(tmp_path):
    _profile(tmp_path)
    path = write(tmp_path, _QUERIES + """
scoring:
  profile: profile.md
  backend: gpt
""")
    with pytest.raises(ConfigError, match="backend") as info:
        load_config(path)
    assert "opencode" in str(info.value)
    assert "claude" in str(info.value)


@pytest.mark.parametrize("value", ["0", "11", '"5"', "true"])
def test_scoring_threshold_must_be_1_to_10(tmp_path, value):
    _profile(tmp_path)
    path = write(tmp_path, _QUERIES + f"""
scoring:
  profile: profile.md
  threshold: {value}
""")
    with pytest.raises(ConfigError, match="threshold"):
        load_config(path)


def test_scoring_unknown_keys_rejected(tmp_path):
    _profile(tmp_path)
    path = write(tmp_path, _QUERIES + """
scoring:
  profile: profile.md
  treshold: 5
""")
    with pytest.raises(ConfigError, match="treshold") as info:
        load_config(path)
    for field in ("enabled", "profile", "threshold", "model", "backend"):
        assert field in str(info.value)


def test_telegram_is_off_by_default(tmp_path):
    path = write(tmp_path, 'queries:\n  - name: "x"\n    text: "python"\n')

    assert load_config(path).telegram is False


def test_telegram_can_be_turned_on(tmp_path):
    path = write(tmp_path, 'telegram: true\nqueries:\n  - name: "x"\n    text: "python"\n')

    assert load_config(path).telegram is True


@pytest.mark.parametrize("value", ['"yes"', "1", "null"])
def test_telegram_must_be_boolean(tmp_path, value):
    path = write(tmp_path, f'telegram: {value}\nqueries:\n  - name: "x"\n    text: "python"\n')

    with pytest.raises(ConfigError, match="telegram"):
        load_config(path)


def test_missing_file_points_to_setup(tmp_path):
    with pytest.raises(ConfigError, match="пройдите настройку"):
        load_config(tmp_path / "nope.yaml")
