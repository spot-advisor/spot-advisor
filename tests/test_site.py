"""Keep the formal comparison beside the install actions on both Pages editions."""

from html.parser import HTMLParser
from pathlib import Path
import runpy

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
    assert sections.top_level[:2] == ["top", "evidence"]
    assert sections.hero_tables == 1
