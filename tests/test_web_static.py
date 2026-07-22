from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "auditcov_mcp" / "web_static"


class _ElementCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.by_id: dict[str, tuple[str, dict[str, str | None]]] = {}

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = dict(attrs)
        element_id = values.get("id")
        if element_id is not None:
            self.by_id[element_id] = (tag, values)


class WebStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        cls.javascript = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        cls.css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")

    def test_work_area_resizer_has_accessible_structure(self) -> None:
        parser = _ElementCollector()
        parser.feed(self.html)

        self.assertIn("workArea", parser.by_id)
        self.assertIn("workAreaResizer", parser.by_id)
        _, attributes = parser.by_id["workAreaResizer"]
        self.assertEqual(attributes.get("role"), "separator")
        self.assertEqual(attributes.get("aria-orientation"), "vertical")
        self.assertEqual(attributes.get("tabindex"), "0")

        left_column = self.html.index('class="left-column"')
        resizer = self.html.index('id="workAreaResizer"')
        file_panel = self.html.index('class="file-panel"')
        self.assertLess(left_column, resizer)
        self.assertLess(resizer, file_panel)

    def test_work_area_resizer_wires_pointer_keyboard_and_persistence(self) -> None:
        self.assertRegex(self.javascript, r'["\']workArea["\']')
        self.assertRegex(self.javascript, r'["\']workAreaResizer["\']')
        for event_name in (
            "pointerdown",
            "pointermove",
            "pointerup",
            "pointercancel",
            "lostpointercapture",
        ):
            self.assertRegex(
                self.javascript,
                rf"addEventListener\(\s*['\"]{event_name}['\"]",
            )
        self.assertIn("setPointerCapture", self.javascript)
        self.assertIn("releasePointerCapture", self.javascript)

        self.assertRegex(
            self.javascript, r"addEventListener\(\s*['\"]keydown['\"]"
        )
        self.assertIn("ArrowLeft", self.javascript)
        self.assertIn("ArrowRight", self.javascript)
        self.assertIn("event.isPrimary", self.javascript)

        self.assertIn("--left-column-width", self.javascript)
        self.assertRegex(self.javascript, r"leftColumnWidth\s*:")
        self.assertIn("saved.leftColumnWidth", self.javascript)
        self.assertIn("if (!workAreaIsResizable()) return;", self.javascript)
        self.assertIn(
            "setLeftColumnWidth(state.leftColumnWidth ?? renderedLeftColumnWidth(), false)",
            self.javascript,
        )

    def test_work_area_resizer_styles_desktop_dragging_and_mobile(self) -> None:
        desktop_css = self.css.split("@media", 1)[0]
        work_area_rule = re.search(r"\.work-area\s*\{(?P<body>[^}]*)\}", desktop_css)
        self.assertIsNotNone(work_area_rule)
        assert work_area_rule is not None
        self.assertIn("var(--left-column-width", work_area_rule.group("body"))
        self.assertRegex(work_area_rule.group("body"), r"minmax\(\s*0\s*,\s*1fr\s*\)")

        resizer_rule = re.search(
            r"(?:#workAreaResizer|\.work-area-resizer)\s*\{(?P<body>[^}]*)\}",
            desktop_css,
        )
        self.assertIsNotNone(resizer_rule)
        assert resizer_rule is not None
        self.assertRegex(resizer_rule.group("body"), r"cursor\s*:\s*col-resize")
        self.assertRegex(resizer_rule.group("body"), r"touch-action\s*:\s*none")
        self.assertRegex(desktop_css, r"user-select\s*:\s*none")

        mobile_css = self.css[self.css.index("@media (max-width: 980px)") :]
        self.assertRegex(
            mobile_css,
            r"(?:#workAreaResizer|\.work-area-resizer)\s*\{[^}]*display\s*:\s*none",
        )


if __name__ == "__main__":
    unittest.main()
