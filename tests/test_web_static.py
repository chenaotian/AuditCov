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

    def javascript_function(self, name: str) -> str:
        match = re.search(
            rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
            self.javascript,
        )
        self.assertIsNotNone(match, f"missing JavaScript function: {name}")
        assert match is not None
        start = self.javascript.index("{", match.start())
        depth = 0
        for index in range(start, len(self.javascript)):
            character = self.javascript[index]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    return self.javascript[start + 1 : index]
        self.fail(f"unterminated JavaScript function: {name}")

    def javascript_call_closure(self, name: str) -> str:
        pending = [name]
        visited: set[str] = set()
        bodies = []
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            try:
                body = self.javascript_function(current)
            except AssertionError:
                continue
            bodies.append(body)
            for called in re.findall(r"\b([A-Za-z_$][\w$]*)\s*\(", body):
                if called not in visited:
                    pending.append(called)
        return "\n".join(bodies)

    def project_card_variable(self, class_name: str) -> str:
        match = re.search(
            rf"\b([A-Za-z_$][\w$]*)\.className\s*=\s*[`'\"]{re.escape(class_name)}",
            self.javascript,
        )
        self.assertIsNotNone(match, f"missing dynamic .{class_name} element")
        assert match is not None
        return match.group(1)

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

    def test_project_delete_uses_sibling_select_and_delete_buttons(self) -> None:
        item = self.project_card_variable("project-item")
        select_button = self.project_card_variable("project-select")
        delete_button = self.project_card_variable("project-delete")

        self.assertRegex(
            self.javascript,
            rf"\b{re.escape(item)}\s*=\s*document\.createElement\(\s*['\"]div['\"]\s*\)",
        )
        for variable in (select_button, delete_button):
            self.assertRegex(
                self.javascript,
                rf"\b{re.escape(variable)}\s*=\s*document\.createElement"
                r"\(\s*['\"]button['\"]\s*\)",
            )

        appended_together = re.search(
            rf"{re.escape(item)}\.append\(\s*{re.escape(select_button)}\s*,\s*"
            rf"{re.escape(delete_button)}\s*\)",
            self.javascript,
        )
        appended_separately = all(
            re.search(
                rf"{re.escape(item)}\.appendChild\(\s*{re.escape(variable)}\s*\)",
                self.javascript,
            )
            for variable in (select_button, delete_button)
        )
        self.assertTrue(
            appended_together or appended_separately,
            "project-select and project-delete must be sibling controls",
        )

    def test_project_delete_requires_confirmation_and_disables_request_button(self) -> None:
        delete_button = self.project_card_variable("project-delete")
        lower_javascript = self.javascript.lower()

        self.assertIn("window.confirm", self.javascript)
        self.assertRegex(
            lower_javascript,
            re.compile(
                r"(?:source|repository)(?: code)? files?[\s\S]{0,200}?"
                r"(?:will|are|is|do|does) not (?:be )?deleted"
                r"|will not delete (?:the )?(?:source|repository)(?: code)? files?"
            ),
        )
        self.assertIn("cannot be undone", lower_javascript)
        self.assertRegex(
            self.javascript,
            r"method\s*:\s*['\"]DELETE['\"]",
        )
        self.assertRegex(
            self.javascript,
            rf"{re.escape(delete_button)}\.disabled\s*=\s*true",
        )
        self.assertRegex(
            self.javascript,
            rf"{re.escape(delete_button)}\.disabled\s*=\s*false",
        )

    def test_project_delete_clears_current_and_last_project_state(self) -> None:
        delete_project = self.javascript_function("deleteProject")
        self.assertRegex(
            delete_project,
            r"state\.selectedProjectId\s*={2,3}\s*project\.id"
            r"|project\.id\s*={2,3}\s*state\.selectedProjectId",
        )

        required_resets = (
            r"state\.selectedProjectId\s*=\s*null",
            r"state\.selectedSessionIds\s*=\s*new Set\(",
            r"state\.selectedFilePath\s*=\s*null",
            r"state\.detail\s*=\s*null",
            r"state\.coverage\s*=\s*null",
            r"state\.expandedTreePaths\s*=\s*new Set\(",
            r"state\.expandedSessionIds\s*=\s*new Set\(",
        )
        delete_closure = self.javascript_call_closure("deleteProject")
        empty_closure = self.javascript_call_closure("renderEmpty")
        for reset in required_resets:
            self.assertRegex(delete_closure, reset)
            self.assertRegex(empty_closure, reset)
        self.assertIn("saveState()", delete_closure)
        self.assertIn("saveState()", empty_closure)

    def test_project_delete_removes_stale_local_state_before_refresh(self) -> None:
        delete_project = self.javascript_function("deleteProject")
        prune = delete_project.index("state.projects = state.projects.filter")
        render = delete_project.index("renderProjectList()", prune)
        refresh = delete_project.index("await loadProjects()", render)
        self.assertLess(prune, render)
        self.assertLess(render, refresh)
        self.assertIn(
            "if (deletedSelectedProject || !state.projects.length) renderEmpty();",
            delete_project,
        )

    def test_project_delete_styles_cover_hover_focus_and_disabled_states(self) -> None:
        self.assertRegex(self.css, r"\.project-select\s*\{")
        self.assertRegex(self.css, r"\.project-delete\s*\{")
        self.assertRegex(self.css, r"\.project-item:focus-within")
        self.assertRegex(self.css, r"\.project-delete:hover")
        self.assertRegex(self.css, r"\.project-delete:focus-visible")
        self.assertRegex(self.css, r"\.project-delete:disabled")

    def test_file_navigation_defaults_to_directory_and_exposes_sort_controls(self) -> None:
        parser = _ElementCollector()
        parser.feed(self.html)

        directory_tag, directory = parser.by_id["directoryViewButton"]
        files_tag, files = parser.by_id["allFilesViewButton"]
        sort_tag, sort = parser.by_id["fileSortSelect"]
        self.assertEqual((directory_tag, files_tag, sort_tag), ("button", "button", "select"))
        self.assertEqual(directory.get("aria-pressed"), "true")
        self.assertEqual(files.get("aria-pressed"), "false")
        self.assertIn("hidden", sort)
        self.assertIn("maximum single-line read count", sort["aria-label"].lower())
        self.assertRegex(self.javascript, r'fileViewMode\s*:\s*["\']tree["\']')
        self.assertRegex(self.javascript, r'fileSortDirection\s*:\s*["\']desc["\']')

    def test_file_navigation_flat_mode_sorts_by_peak_then_path(self) -> None:
        render_tree = self.javascript_function("renderTree")
        render_all = self.javascript_function("renderAllFiles")
        self.assertIn('state.fileViewMode === "files"', render_tree)
        self.assertIn("collectFileNodes(root)", render_all)
        self.assertIn('state.fileSortDirection === "asc"', render_all)
        self.assertRegex(
            render_all,
            r"fileMaxReadCount\(left\)\s*-\s*fileMaxReadCount\(right\)",
        )
        self.assertRegex(
            render_all,
            r"fileMaxReadCount\(right\)\s*-\s*fileMaxReadCount\(left\)",
        )
        difference_check = render_all.index("if (difference) return difference;")
        path_tie_break = render_all.index(".localeCompare(", difference_check)
        self.assertLess(difference_check, path_tie_break)
        self.assertIn("file.path", render_all)

    def test_file_peak_badges_show_zero_red_and_positive_green_depth(self) -> None:
        render_node = self.javascript_function("renderFileNavigationNode")
        render_badge = self.javascript_function("renderFileReadBadge")
        color = self.javascript_function("fileReadCountColor")
        self.assertIn("fileMaxReadCount(node)", render_node)
        self.assertIn("max_read_count", self.javascript_function("fileMaxReadCount"))
        self.assertIn('count > 0 ? "read" : "unread"', render_badge)
        self.assertIn("--file-read-color", render_badge)
        self.assertIn("Math.exp", color)
        self.assertRegex(
            self.css,
            r"\.file-read-badge\.unread\s*\{[^}]*color\s*:\s*var\(--missed\)",
        )
        self.assertRegex(
            self.css,
            r"\.file-read-badge\s*\{[^}]*color\s*:\s*"
            r"var\(--file-read-color,\s*var\(--covered\)\)",
        )
        badge_position = render_node.index("file-read-badge")
        name_position = render_node.index("tree-name", badge_position)
        self.assertLess(badge_position, name_position)

    def test_file_navigation_preferences_and_accessibility_are_preserved(self) -> None:
        save_state = self.javascript_function("saveState")
        restore_state = self.javascript_function("restoreState")
        tree_node = self.javascript_function("renderTreeNode")
        file_node = self.javascript_function("renderFileNavigationNode")
        for field in ("fileViewMode", "fileSortDirection"):
            self.assertIn(field, save_state)
            self.assertIn(field, restore_state)
        self.assertIn('setAttribute("aria-expanded"', tree_node)
        self.assertIn('setAttribute("aria-current"', file_node)
        self.assertIn('maxReadCount === 1 ? "1 read"', file_node)
        self.assertIn("maximum ${readLabel} on one line", file_node)


if __name__ == "__main__":
    unittest.main()
