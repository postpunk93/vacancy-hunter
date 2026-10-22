"""Слой Claude: skills по этапам, хук начала разговора, CLAUDE.md, шаблоны."""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ("setup", "collect", "apply", "tune")


def frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    assert lines[0] == "---", "skill начинается с frontmatter"
    end = lines.index("---", 1)
    fields = {}
    for line in lines[1:end]:
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


@pytest.mark.parametrize("name", SKILLS)
def test_every_skill_has_name_and_description(name):
    fields = frontmatter((ROOT / ".claude/skills" / name / "SKILL.md").read_text(encoding="utf-8"))
    assert fields.get("name") == name
    assert fields.get("description")


def test_claude_md_imports_the_guide():
    assert "@hunter/corpus/GUIDE.md" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def test_session_start_hook_runs_todo():
    settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    commands = [h["command"] for e in settings["hooks"]["SessionStart"] for h in e["hooks"]]
    assert any("vacancies todo" in c for c in commands)


def test_templates_have_the_three_profile_sections():
    text = (ROOT / "templates/profile.md").read_text(encoding="utf-8")
    for section in ("## Кто я", "## Что ищу", "## Красные флаги"):
        assert section in text


def test_strategy_template_has_its_sections():
    text = (ROOT / "templates/strategy.md").read_text(encoding="utf-8")
    for section in ("## Позиционирование", "## Что сделать до откликов", "## Очередь"):
        assert section in text


def test_no_personal_paths_in_public_files():
    """В публичных файлах нет путей к личной папке владельца."""
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    found = []
    for name in files:
        if name.startswith("docs/superpowers/"):
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        # Склеено, чтобы тест не ловил сам себя.
        for needle in ("job-" + "search", "/" + "home/"):
            if needle in text:
                found.append(f"{name}: {needle}")
    assert not found, found


def test_apply_sample_marker_picks_one_letter():
    """С двумя письмами в draft.md образец тона — только отмеченное письмо."""
    text = (ROOT / ".claude/skills/apply/SKILL.md").read_text(encoding="utf-8")
    opora = text.split("2. **Опора.**")[1].split("3. **Черновик.**")[0]
    assert "(по книге)" in opora and "(по каркасу)" in opora
    assert "без уточнения" in opora
