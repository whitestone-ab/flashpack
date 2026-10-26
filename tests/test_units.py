from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from flashpack.compress import (
    normalize_imports_js,
    normalize_imports_python,
    skeleton_python,
    strip_comments,
    truncate_text,
)
from flashpack.tree import render_tree
from tests.test_spec import run_to, write_project


class CompressionUnitTests(unittest.TestCase):
    def test_python_comments_removed_docstring_kept(self) -> None:
        source = '"""doc # keep"""\n# remove\n\nx = 1  # inline\n\n'
        result = strip_comments(source, "python")
        self.assertIn("doc # keep", result)
        self.assertNotIn("# remove", result)
        self.assertNotIn("# inline", result)
        self.assertNotIn("\n\n", result)

    def test_js_comments_do_not_break_strings(self) -> None:
        source = (
            'const url = "http://example.com"; // comment\n'
            "/* block\n comment */\n"
            "const y = 2;\n"
        )
        result = strip_comments(source, "javascript")
        self.assertIn("http://example.com", result)
        self.assertNotIn("// comment", result)
        self.assertNotIn("block", result)
        self.assertIn("const y = 2", result)

    def test_python_skeleton_markers_and_validity(self) -> None:
        source = 'def add(a, b):\n    """doc"""\n    return a + b\n'
        skeleton = skeleton_python(source)
        assert skeleton is not None
        self.assertIn("# [skeleton:", skeleton)
        self.assertIn("# [body omitted, 1 lines]", skeleton)
        self.assertNotIn("return a + b", skeleton)
        # The marker must not make the skeleton unparsable.
        import ast

        ast.parse(skeleton)

    def test_import_normalization(self) -> None:
        source = "import os\nfrom .core import engine\n\nx = 1\n"
        result = normalize_imports_python(source)
        self.assertIn("# imports: os, .core", result)
        self.assertNotIn("import os", result)

        js = "import { x } from './x';\nconsole.log(x);\n"
        self.assertIn("// imports: ./x", normalize_imports_js(js))

    def test_truncation_marker(self) -> None:
        text = "\n".join(f"x{i} = {i}" for i in range(50))
        result, truncated = truncate_text(text, "python", 10, 50, force=True)
        self.assertTrue(truncated)
        self.assertIn("[truncated: showing", result)
        self.assertIn("of 50 lines", result)


class EmptyProjectTests(unittest.TestCase):
    def test_empty_project_keeps_stable_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "snapshot.xml"
            code, _, err = run_to(root, out)
            self.assertEqual(code, 0, err)
            parsed = ET.parse(out).getroot()
            self.assertEqual(parsed.attrib["files_included"], "0")
            self.assertEqual(parsed.attrib["files_total"], "0")
            self.assertIsNotNone(parsed.find("contents"))
            self.assertEqual(len(parsed.findall("./file-list/file")), 0)
            self.assertIn("（无）", parsed.findtext("tree") or "")

            md = root / "snapshot.md"
            code, _, err = run_to(root, md, "--format", "markdown")
            self.assertEqual(code, 0, err)
            text = md.read_text(encoding="utf-8")
            self.assertIn("## 2.5 依赖索引", text)
            self.assertIn("## 4. 被排除文件", text)
            self.assertIn("（无）", text)


class DefaultIncludeTests(unittest.TestCase):
    def test_default_whitelist_ignores_markdown_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(
                root,
                {
                    "a.py": "x = 1\n",
                    "README.md": "# readme\n",
                    "data.json": '{"a": 1}\n',
                },
            )
            out = root / "snapshot.xml"
            code, _, err = run_to(root, out)
            self.assertEqual(code, 0, err)
            parsed = ET.parse(out).getroot()
            paths = [f.attrib["path"] for f in parsed.findall("./file-list/file")]
            self.assertEqual(paths, ["a.py"])
            self.assertEqual(parsed.attrib["files_total"], "1")
            self.assertEqual(parsed.findall("./skipped/file"), [])

    def test_latin1_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root, {"latin.py": b'name = "caf\xe9"\n'})
            out = root / "snapshot.xml"
            code, _, err = run_to(root, out)
            self.assertEqual(code, 0, err)
            parsed = ET.parse(out).getroot()
            files = parsed.findall("./file-list/file")
            self.assertEqual(len(files), 1)
            content = parsed.findtext("./contents/file") or ""
            self.assertIn("café", content)

    def test_js_fallback_warning_is_emitted_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(
                root,
                {
                    "a.js": "export function a() { return 1; }\n",
                    "b.js": "export function b() { return 2; }\n",
                },
            )
            out = root / "snapshot.xml"
            code, _, err = run_to(root, out)
            self.assertEqual(code, 0, err)
            parsed = ET.parse(out).getroot()
            statuses = {f.attrib["status"] for f in parsed.findall("./file-list/file")}
            if "tree-sitter" in err:
                self.assertEqual(err.count("tree-sitter"), 1)
                self.assertNotIn("skeleton", statuses)
            else:
                # If grammars are already downloaded, JS skeleton is allowed.
                self.assertIn("skeleton", statuses)


class TreeUnitTests(unittest.TestCase):
    def test_tree_ellipsis_and_root(self) -> None:
        text = render_tree(
            ["a/b/c/d/direct.py", "a/b/c/d/e/f.py", "a/top.py"], "proj", 4
        )
        self.assertTrue(text.startswith("proj/"))
        self.assertIn("...", text)
        self.assertIn("direct.py", text)
        self.assertNotIn("f.py", text)
        self.assertIn("top.py", text)


if __name__ == "__main__":
    unittest.main()
