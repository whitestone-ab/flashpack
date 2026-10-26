from __future__ import annotations

import io
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from flashpack.cli import build_parser, main
from flashpack.config import load_config
from flashpack.ignore import IgnoreMatcher

# Built at runtime so no realistic-looking cloud key appears in the repository.
FAKE_AWS = "AKIA" + "X" * 16


def write_project(root: Path, files: dict[str, str | bytes]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")


def run(root: Path, *args: str) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main([str(root), *args])
    return code, out.getvalue(), err.getvalue()


def run_to(root: Path, out: Path, *args: str) -> tuple[int, str, str]:
    return run(root, "--output", str(out), *args)


class XmlAcceptanceTests(unittest.TestCase):
    def project(self, root: Path) -> None:
        write_project(
            root,
            {
                "src/core/types.py": "class Types:\n    pass\n",
                "src/core/engine.py": (
                    "from .types import Types\n\n"
                    "class Engine:\n    def run(self):\n        return Types()\n"
                ),
                "src/utils/helpers.py": "def helper():\n    return 1\n",
                "src/main.py": (
                    "from core.engine import Engine\n"
                    "from utils.helpers import helper\n\n"
                    "def main():\n    return Engine(), helper()\n"
                ),
                "tests/test_engine.py": (
                    "from core.engine import Engine\n\n"
                    "def test_engine():\n    assert Engine()\n"
                ),
                "web/util.js": "export function util() { return 1; }\n",
                "web/util.ts": "export function utilTs(): number { return 2; }\n",
                "web/index.js": "import { util } from './util.js';\nconsole.log(util());\n",
                "web/app.ts": "import { utilTs } from './util';\nconsole.log(utilTs());\n",
            },
        )

    def test_xml_structure_fids_dependencies_and_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.project(root)
            out = root / "snapshot.xml"
            code, _, err = run_to(root, out)
            self.assertEqual(code, 0, err)
            self.assertNotIn("generated_at", out.read_text(encoding="utf-8"))
            xml_root = ET.parse(out).getroot()
            self.assertEqual(xml_root.tag, "snapshot")
            for attr in (
                "project",
                "files_included",
                "files_total",
                "total_lines",
                "estimated_tokens",
                "budget",
                "sort",
                "skeleton",
            ):
                self.assertIn(attr, xml_root.attrib)
            self.assertEqual(xml_root.attrib["sort"], "refs-desc")
            self.assertEqual(xml_root.attrib["skeleton"], "on")

            protocol = xml_root.find("protocol")
            self.assertIsNotNone(protocol)
            assert protocol is not None
            self.assertEqual(
                [child.tag for child in list(protocol)],
                ["coverage", "status-legend", "reference-rule", "reading-order", "warning"],
            )

            files = xml_root.findall("./file-list/file")
            self.assertEqual(len(files), 9)
            self.assertEqual(
                [f.attrib["id"] for f in files],
                [f"F{index:03d}" for index in range(1, 10)],
            )
            self.assertEqual(
                [child.tag for child in list(xml_root)[:4]],
                ["protocol", "tree", "file-list", "contents"],
            )
            self.assertEqual(files[0].attrib["path"], "src/core/engine.py")
            self.assertEqual(files[0].attrib["refs"], "2")
            self.assertEqual(files[0].attrib["id"], "F001")
            # refs ties are path-ordered.
            tie_paths = [f.attrib["path"] for f in files[1:5]]
            self.assertEqual(tie_paths, sorted(tie_paths))
            for f in files:
                self.assertEqual(
                    list(f.attrib.keys()),
                    ["id", "path", "lang", "lines", "tokens", "status", "refs", "depends-on"],
                )
                self.assertIn(f.attrib["status"], {"full", "skeleton", "truncated", "omitted"})
                self.assertNotIn(" ", f.attrib["depends-on"])
            contents = xml_root.findall("./contents/file")
            for f in contents:
                self.assertEqual(list(f.attrib.keys()), ["id", "path", "status"])
            self.assertEqual(len(contents), 9)
            main_file = next(f for f in files if f.attrib["path"] == "src/main.py")
            self.assertEqual(main_file.attrib["depends-on"], "F001,F003")

    def test_markdown_structure_table_and_dependency_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.project(root)
            out = root / "snapshot.md"
            code, _, err = run_to(root, out, "--format", "markdown")
            self.assertEqual(code, 0, err)
            text = out.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# 项目快照："))
            self.assertIn("## 0. 阅读协议", text)
            protocol = text.split("## 0. 阅读协议", 1)[1].split("## 1.", 1)[0]
            self.assertEqual(protocol.count("\n- "), 5)
            self.assertIn("## 1. 目录树", text)
            self.assertIn("## 2. 文件清单", text)
            self.assertIn(
                "| ID | 路径 | 语言 | 行数 | Tokens | 状态 | 被引用次数 |", text
            )
            self.assertIn("## 2.5 依赖索引", text)
            self.assertIn("## 3. 文件内容", text)
            self.assertIn("### [F001] src/core/engine.py", text)
            self.assertNotIn("depends-on", text.split("## 2.5", 1)[0])
            self.assertIn("````", text)
            self.assertIn("## 4. 被排除文件", text)
            order = [
                text.index("## 0. 阅读协议"),
                text.index("## 1. 目录树"),
                text.index("## 2. 文件清单"),
                text.index("## 2.5 依赖索引"),
                text.index("## 3. 文件内容"),
                text.index("## 4. 被排除文件"),
            ]
            self.assertEqual(order, sorted(order))


class SkeletonTests(unittest.TestCase):
    def test_skeleton_default_and_no_skeleton(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(
                root,
                {
                    "app.py": 'def add(a, b):\n    """doc"""\n    return a + b\n',
                    "web/util.js": "export function util() { return 1; }\n",
                },
            )
            out = root / "a.xml"
            code, _, err = run_to(root, out)
            self.assertEqual(code, 0, err)
            self.assertIn("skeleton", out.read_text(encoding="utf-8"))

            out2 = root / "b.xml"
            code, _, err2 = run_to(root, out2, "--no-skeleton")
            self.assertEqual(code, 0, err2)
            parsed = ET.parse(out2).getroot()
            statuses = {f.attrib["status"] for f in parsed.findall("./file-list/file")}
            self.assertNotIn("skeleton", statuses)


class FilteringTests(unittest.TestCase):
    def build(self, root: Path) -> None:
        write_project(
            root,
            {
                ".gitignore": "ignored.py\n",
                "src/app.py": "x = 1\n",
                "src/secret.py": f'AWS = "{FAKE_AWS}"\n',
                "src/empty.py": "",
                "src/bin.py": b"A\x00B",
                "src/skip.py": "x = 1\n",
                "src/big.py": "\n".join(f"x{i} = {i}" for i in range(300)),
                "src/ignored.py": "x = 1\n",
                "package-lock.json": '{"lockfileVersion": 3}\n',
                "docs/readme.md": "# docs\n",
                "node_modules/pkg.js": "x = 1\n",
            },
        )

    def test_skipped_reasons_secret_and_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.build(root)
            out = root / "snapshot.xml"
            code, _, err = run_to(
                root,
                out,
                "--include",
                "*.py,*.json",
                "--exclude",
                "skip.py",
                "--max-file-size",
                "200",
            )
            self.assertEqual(code, 0, err)
            text = out.read_text(encoding="utf-8")
            self.assertNotIn(FAKE_AWS, text)
            self.assertIn("密钥扫描命中", err)
            parsed = ET.parse(out).getroot()
            files = parsed.findall("./file-list/file")
            self.assertEqual(len(files), 2)
            self.assertEqual({f.attrib["status"] for f in files}, {"full", "truncated"})
            big = next(f for f in files if f.attrib["path"] == "src/big.py")
            self.assertEqual(big.attrib["status"], "truncated")
            skipped = parsed.findall("./skipped/file")
            self.assertEqual(
                [(f.attrib["path"], f.attrib["reason"]) for f in skipped],
                [
                    ("package-lock.json", "lockfile"),
                    ("src/bin.py", "binary"),
                    ("src/empty.py", "empty"),
                    ("src/secret.py", "secret"),
                    ("src/skip.py", "excluded"),
                ],
            )
            self.assertTrue(all("id" not in f.attrib for f in skipped))
            self.assertIn("truncated: showing", text)
            child_tags = [child.tag for child in list(parsed)]
            self.assertEqual(
                child_tags[:4], ["protocol", "tree", "file-list", "contents"]
            )
            self.assertEqual(child_tags[4], "skipped")

    def test_no_secret_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root, {"secret.py": f'AWS = "{FAKE_AWS}"\n'})
            out = root / "snapshot.xml"
            code, _, err = run_to(root, out, "--no-secret-scan")
            self.assertEqual(code, 0, err)
            parsed = ET.parse(out).getroot()
            self.assertEqual(len(parsed.findall("./file-list/file")), 1)
            self.assertIn(FAKE_AWS, out.read_text(encoding="utf-8"))

    def test_gitignore_is_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.build(root)
            matcher = IgnoreMatcher.from_file(root / ".gitignore")
            self.assertTrue(matcher.matches("src/ignored.py"))
            out = root / "snapshot.xml"
            code, _, _ = run_to(root, out)
            self.assertEqual(code, 0)
            parsed = ET.parse(out).getroot()
            paths = {f.attrib["path"] for f in parsed.findall("./file-list/file")}
            self.assertNotIn("src/ignored.py", paths)


class BudgetTests(unittest.TestCase):
    def test_low_budget_list_mode_exit_zero_when_it_fits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root, {"a.py": "x = 1\n"})
            out = root / "snapshot.xml"
            code, _, err = run_to(root, out, "--budget", "500")
            self.assertEqual(code, 0, err)
            parsed = ET.parse(out).getroot()
            self.assertIsNotNone(parsed.find("contents"))
            self.assertEqual(len(parsed.findall("./contents/file")), 0)
            statuses = {f.attrib["status"] for f in parsed.findall("./file-list/file")}
            self.assertEqual(statuses, {"omitted"})
            self.assertEqual(parsed.findall("./skipped/file"), [])
            self.assertIn("预算极低", out.read_text(encoding="utf-8"))

    def test_impossible_budget_exit_four_still_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root, {"a.py": "x = 1\n"})
            out = root / "snapshot.xml"
            code, _, err = run_to(root, out, "--budget", "1")
            self.assertEqual(code, 4, err)
            self.assertTrue(out.exists())
            self.assertIn("budget 低于 1000", err)
            parsed = ET.parse(out).getroot()
            self.assertIsNotNone(parsed.find("contents"))

    def test_stage_one_omits_low_refs_and_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {"core.py": "def core():\n    return 1\n"}
            for index in range(20):
                files[f"mod_{index}.py"] = "from core import core\n" + "".join(
                    f"def f_{index}_{step}():\n    return {step}\n" for step in range(3)
                )
            write_project(root, files)
            out = root / "snapshot.xml"
            code, _, err = run_to(root, out, "--budget", "1200", "--no-skeleton")
            self.assertEqual(code, 0, err)
            parsed = ET.parse(out).getroot()
            files_xml = parsed.findall("./file-list/file")
            statuses = [f.attrib["status"] for f in files_xml]
            self.assertIn("omitted", statuses)
            self.assertGreater(len(parsed.findall("./contents/file")), 0)
            core = next(f for f in files_xml if f.attrib["path"] == "core.py")
            self.assertEqual(core.attrib["status"], "full")
            self.assertGreater(len(parsed.findall("./skipped/file")), 0)

    def test_single_oversized_file_is_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(
                root,
                {"big.py": "".join(f"value_{i} = {i}\n" for i in range(2000))},
            )
            out = root / "snapshot.xml"
            code, _, err = run_to(root, out, "--budget", "1000", "--no-skeleton")
            self.assertEqual(code, 0, err)
            parsed = ET.parse(out).getroot()
            files = parsed.findall("./file-list/file")
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].attrib["status"], "truncated")
            self.assertEqual(len(parsed.findall("./contents/file")), 1)

    def test_stage_two_list_mode_removes_budget_skips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {"core.py": "def core():\n    return 1\n"}
            for index in range(30):
                files[f"mod_{index}.py"] = "from core import core\n" + "".join(
                    f"def f_{index}_{step}():\n    return {step}\n" for step in range(3)
                )
            write_project(root, files)
            out = root / "snapshot.xml"
            code, _, err = run_to(root, out, "--budget", "1000", "--no-skeleton")
            self.assertIn(code, (0, 4), err)
            parsed = ET.parse(out).getroot()
            statuses = {f.attrib["status"] for f in parsed.findall("./file-list/file")}
            self.assertEqual(statuses, {"omitted"})
            self.assertEqual(len(parsed.findall("./contents/file")), 0)
            self.assertEqual(parsed.findall("./skipped/file"), [])
            coverage = parsed.findtext("./protocol/coverage") or ""
            self.assertIn("清单模式", coverage)


class ExitCodeTests(unittest.TestCase):
    def test_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root, {"a.py": "x = 1\n"})
            out = root / "snapshot.xml"
            self.assertEqual(run_to(root, out)[0], 0)
            self.assertEqual(run(root, "--budget", "0")[0], 1)
            self.assertEqual(run(root, "--nope")[0], 1)
            self.assertEqual(run(root / "missing")[0], 2)
            self.assertEqual(run(root, "--output", str(root))[0], 3)
            self.assertEqual(run_to(root, out, "--budget", "1")[0], 4)

    def test_invalid_target_is_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing"
            code, _, _ = run(missing)
            self.assertEqual(code, 2)
            file_target = root / "a.py"
            file_target.write_text("x = 1\n", encoding="utf-8")
            code, _, _ = run(file_target)
            self.assertEqual(code, 2)


class DeterminismTests(unittest.TestCase):
    def test_default_is_byte_identical_and_timestamp_is_conditional(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root, {"a.py": "def f():\n    return 1\n"})
            one = root / "one.xml"
            two = root / "two.xml"
            self.assertEqual(run_to(root, one)[0], 0)
            self.assertEqual(run_to(root, two)[0], 0)
            self.assertEqual(one.read_bytes(), two.read_bytes())

            stamped = root / "stamped.xml"
            code, _, err = run_to(root, stamped, "--timestamp")
            self.assertEqual(code, 0, err)
            text = stamped.read_text(encoding="utf-8")
            match = re.search(r'generated_at="([^"]+)"', text)
            self.assertIsNotNone(match)
            assert match is not None
            self.assertRegex(match.group(1), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
            self.assertEqual(
                re.sub(r' generated_at="[^"]*"', "", text), one.read_text(encoding="utf-8")
            )


class ConfigTests(unittest.TestCase):
    def test_toml_and_cli_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(
                root,
                {
                    "a.py": "def f():\n    return 1\n",
                    ".flashpack.toml": (
                        "[flashpack]\n"
                        'format = "markdown"\n'
                        "budget = 500\n"
                        'include = ["*.py"]\n'
                        "skeleton = false\n"
                    ),
                },
            )
            out = root / "out.md"
            code, _, err = run_to(root, out, "--budget", "32000")
            self.assertEqual(code, 0, err)
            text = out.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# 项目快照："))
            table = text.split("## 2. 文件清单", 1)[1].split("## 2.5", 1)[0]
            self.assertIn("| F001 | a.py | python |", table)
            self.assertIn("| full |", table)
            self.assertNotIn("| skeleton |", table)

            parser = build_parser()
            cfg = load_config(parser.parse_args([str(root), "--budget", "32000"]))
            self.assertEqual(cfg.budget, 32000)

    def test_invalid_toml_warns_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(
                root,
                {
                    "a.py": "x = 1\n",
                    ".flashpack.toml": "this is not toml",
                },
            )
            out = root / "out.xml"
            code, _, err = run_to(root, out)
            self.assertEqual(code, 0, err)
            self.assertIn("解析失败", err)
            self.assertEqual(ET.parse(out).getroot().tag, "snapshot")


class TreeAndRenderingTests(unittest.TestCase):
    def test_tree_depth_four(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root, {"a/b/c/d/e/f.py": "x = 1\n"})
            out = root / "out.md"
            code, _, err = run_to(root, out, "--format", "markdown")
            self.assertEqual(code, 0, err)
            tree = out.read_text(encoding="utf-8").split("## 1. 目录树", 1)[1].split("## 2.", 1)[0]
            self.assertIn("...", tree)
            self.assertNotIn("f.py", tree)

            out2 = root / "out2.md"
            code, _, err = run_to(
                root, out2, "--format", "markdown", "--tree-depth", "2"
            )
            self.assertEqual(code, 0, err)
            tree2 = out2.read_text(encoding="utf-8").split("## 1. 目录树", 1)[1].split("## 2.", 1)[0]
            self.assertIn("...", tree2)
            self.assertNotIn("c/", "\n".join(
                line for line in tree2.splitlines() if line.strip().startswith(("├──", "└──", "│"))
            ))

    def test_cdata_and_backtick_fence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root, {"a.py": 'x = "]]>"\ny = "````"\n'})
            xml_out = root / "out.xml"
            code, _, err = run_to(root, xml_out, "--no-skeleton")
            self.assertEqual(code, 0, err)
            parsed = ET.parse(xml_out).getroot()
            content = parsed.find("./contents/file")
            self.assertIsNotNone(content)
            assert content is not None
            self.assertIn("]]>", content.text or "")

            md_out = root / "out.md"
            code, _, err = run_to(root, md_out, "--format", "markdown", "--no-skeleton")
            self.assertEqual(code, 0, err)
            text = md_out.read_text(encoding="utf-8")
            self.assertIn("`````python", text)


class OutputStructureTests(unittest.TestCase):
    def test_protocol_five_lines_and_skipped_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_project(root, {"a.py": "x = 1\n", "secret.py": f'k = "{FAKE_AWS}"\n'})
            out = root / "out.md"
            code, _, err = run_to(root, out, "--format", "markdown")
            self.assertEqual(code, 0, err)
            text = out.read_text(encoding="utf-8")
            protocol = text.split("## 0. 阅读协议", 1)[1].split("## 1.", 1)[0]
            self.assertEqual(protocol.count("\n- "), 5)
            self.assertIn("## 4. 被排除文件", text)
            skipped = text.split("## 4. 被排除文件", 1)[1]
            self.assertIn("secret.py — secret", skipped)


if __name__ == "__main__":
    unittest.main()
