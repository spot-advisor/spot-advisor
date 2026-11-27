"""Keep the formal comparison beside the install actions on both Pages editions."""

from html.parser import HTMLParser
from pathlib import Path
import runpy
from zipfile import ZipFile

import pytest


SITE = Path(__file__).resolve().parents[1] / "site"


class PageSections(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sections = []
        self.top_level = []
        self.hero_tables = 0

    def handle_starttag(self, tag, attrs):
        if tag == "section":
            section_id = dict(attrs).get("id")
            if not self.sections:
                self.top_level.append(section_id)
            self.sections.append(section_id)
        if tag == "table" and self.sections[:1] == ["top"]:
            self.hero_tables += 1

    def handle_endtag(self, tag):
        if tag == "section":
            self.sections.pop()


@pytest.mark.parametrize("page", ["index.html", "zh-CN/index.html"])
def test_formal_benchmark_opens_the_page(page):
    values = runpy.run_path(str(SITE / "build.py"))["scorecard"]()
    rendered = (SITE / page).read_text(encoding="utf-8").format_map(values)
    sections = PageSections()
    sections.feed(rendered)
    assert sections.top_level[:4] == ["top", "install", "repair-study", "evidence"]
    assert sections.hero_tables == 1
    hero = rendered.split('id="install"', 1)[0]
    assert all(label in hero for label in ("$82.82", "$4.22", "94.9%", "39 / 48", "31 / 48"))
    assert all(label in rendered for label in ("$61.15", "$3.50", "94.3%"))
    costs = values["repair"]
    assert (costs["n"], costs["matched_n"]) == (48, 40)
    assert costs["missing"] == {"astra_only": 4, "spot_advisor": 8}
    assert costs["arms"]["spot_advisor"]["tasks_with_consultation"] == 17


def test_download_contains_only_the_installable_skill(tmp_path):
    archive = tmp_path / "advisor.zip"
    runpy.run_path(str(SITE / "build.py"))["write_skill_zip"](archive)
    with ZipFile(archive) as package:
        assert set(package.namelist()) == {
            "advisor/SKILL.md", "advisor/agents/openai.yaml", "advisor/scripts/ask_expert.py",
        }
        for member in package.namelist():
            source = SITE.parent / "skills" / member
            assert package.read(member).decode() == source.read_text(encoding="utf-8")
