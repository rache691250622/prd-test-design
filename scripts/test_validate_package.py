"""Regression tests for portable Markdown heading links; never edit user packages."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import validate_package as v


class PackageLinksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name, text):
        (self.root / name).write_text(text, encoding="utf-8")

    def make_package(self, sequence=False):
        required = v.SEQUENCE_REQUIRED_FILES if sequence else v.STANDARD_REQUIRED_FILES
        for name in required:
            self.write(name, "# 文档\n")
        scn = "07-测试场景.md" if sequence else "06-测试场景.md"
        tc = "08-测试用例.md" if sequence else "07-测试用例.md"
        self.master = self.root / tc
        self.write("03-需求规格.md", "### SRC-001\n来源\n\n### SPEC-001\n需求\n")
        self.write("04-业务规则.md", "### RULE-001\n规则\n")
        self.write("05-ITEM-POINT-拆分.md", "### ITEM-001\n模块\n\n### POINT-001\n操作\n")
        trace = "；".join([
            "[ITEM-001](05-ITEM-POINT-拆分.md#item-001)",
            "[POINT-001](05-ITEM-POINT-拆分.md#point-001)",
            "[RULE-001](04-业务规则.md#rule-001)",
            "[SPEC-001](03-需求规格.md#spec-001)",
        ])
        self.write(scn, f"### SCN-001\n\n{trace}\n\n[TC-001]({tc}#tc-001)\n")
        body = f"""# 主用例

[TC-001](#tc-001)

## TC-001

**标题**：导入超过 100 个礼包时整份拒绝

| 字段 | 值 |
|---|---|
| 优先级 | P1 |
| 追踪关系 | {trace}；[SCN-001]({scn}#scn-001) |

### 前置条件

合法文件身份、可用测试分区。

### 测试数据

| 名称 | 值 |
|---|---|
| 礼包数 | 101 |

### 操作步骤与预期结果

| 步骤 | 操作 | 预期结果 |
|---|---|---|
| 1 | 上传文件 | 数量超限，整份拒绝，创建数为零 |

### 测试结果

- [ ] 通过
- [ ] 不通过
"""
        self.write(tc, body)
        high = "09-高价值用例筛选.md" if sequence else "08-高价值用例筛选.md"
        self.write(high, f"# 核心\n\n[TC-001]({tc}#tc-001)\n")
        if sequence:
            self.write("06-时序图.md", "# 程序时序图\n\n### SEQ-001\n\n[POINT-001](05-ITEM-POINT-拆分.md#point-001)\n\n```mermaid\nsequenceDiagram\n客户端->>服务: 上传\n服务-->>客户端: 失败，不写入\n```\n")
        return required

    def run_cli(self):
        output = io.StringIO()
        with patch("sys.argv", ["validate_package.py", "--artifact", str(self.root)]), contextlib.redirect_stdout(output):
            result = v.main()
        return result, output.getvalue()

    def test_standard_package_native_headings_and_title_extraction(self):
        self.make_package()
        self.assertEqual(self.run_cli()[0], 0)
        self.assertEqual(v.markdown_case_titles(self.master), {"TC-001": "导入超过 100 个礼包时整份拒绝"})
        self.assertEqual(v.markdown_case_statuses(self.master), {"TC-001": "未测试"})

    def test_sequence_package_uses_same_native_target_contract(self):
        self.make_package(sequence=True)
        self.assertEqual(self.run_cli()[0], 0)

    def test_legacy_html_anchor_and_long_heading_are_rejected(self):
        self.make_package()
        text = self.master.read_text(encoding="utf-8").replace("## TC-001\n\n**标题**：导入超过 100 个礼包时整份拒绝", '<a id="tc-001"></a>\n\n## TC-001：导入超过 100 个礼包时整份拒绝')
        self.write(self.master.name, text)
        code, output = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("real Markdown heading", output)
        self.assertIn("instead of HTML anchors", output)

    def test_missing_business_title_is_rejected(self):
        self.make_package()
        self.write(self.master.name, self.master.read_text(encoding="utf-8").replace("**标题**：导入超过 100 个礼包时整份拒绝", ""))
        self.assertIn("missing separate business title", self.run_cli()[1])

    def test_missing_target_file_is_rejected(self):
        self.make_package()
        (self.root / "04-业务规则.md").unlink()
        code, output = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("missing link target", output)

    def test_missing_heading_is_rejected_even_with_html_id(self):
        self.make_package()
        self.write("04-业务规则.md", '<a id="rule-001"></a>\n\n### RULE-001：规则名称\n')
        self.assertIn("real Markdown heading", self.run_cli()[1])

    def test_duplicate_headings_cannot_silently_overwrite_cases(self):
        self.make_package()
        self.write(self.master.name, self.master.read_text(encoding="utf-8") + "\n## TC-001\n\n**标题**：重复用例\n")
        self.assertIn("duplicate native heading", self.run_cli()[1])

    def test_code_fence_cannot_supply_a_link_target(self):
        self.make_package()
        self.write("04-业务规则.md", "```markdown\n### RULE-001\n```\n")
        self.assertIn("real Markdown heading", self.run_cli()[1])

    def test_code_examples_do_not_create_broken_links_or_cases(self):
        self.make_package()
        examples = "\n~~~markdown\n## TC-999\n[TC-999](missing.md#tc-999)\n~~~\n\n`[RULE-999](missing.md#rule-999)`\n"
        self.write(self.master.name, self.master.read_text(encoding="utf-8") + examples)
        self.assertEqual(self.run_cli()[0], 0)
        self.assertEqual(v.markdown_case_ids(self.master), {"TC-001"})

    def test_business_title_changes_do_not_break_incoming_links(self):
        self.make_package()
        self.write(self.master.name, self.master.read_text(encoding="utf-8").replace("导入超过 100 个礼包时整份拒绝", "同 ID 礼包已存在时不覆盖"))
        self.assertEqual(self.run_cli()[0], 0)

    def test_label_cannot_link_to_another_existing_id(self):
        self.make_package()
        self.write("04-业务规则.md", "### RULE-001\n\n### RULE-002\n")
        self.write("08-高价值用例筛选.md", "[RULE-001](04-业务规则.md#rule-002)\n")
        self.assertIn("label and target ID differ", self.run_cli()[1])

    def test_source_reference_requires_native_target_without_editing_prd(self):
        self.make_package()
        self.write("00-需求文档.md", '<a id="source-1"></a>\n\n# 原始需求\n')
        self.write("02-memory-log.md", "[来源](00-需求文档.md#source-1)\n")
        self.assertIn("real Markdown heading", self.run_cli()[1])
        self.write("02-memory-log.md", "[来源](00-需求文档.md)\n")
        self.assertEqual(self.run_cli()[0], 0)

    def test_missing_sequence_file_reports_error_not_exception(self):
        self.make_package(sequence=True)
        (self.root / "07-测试场景.md").unlink()
        self.assertEqual(self.run_cli()[0], 1)

    def test_percent_encoded_same_file_id_resolves(self):
        required = self.make_package()
        self.write("08-高价值用例筛选.md", "[TC-001](07-%E6%B5%8B%E8%AF%95%E7%94%A8%E4%BE%8B.md#%74c-001)\n")
        self.assertEqual(v.validate_internal_links(self.root, required), [])


if __name__ == "__main__":
    unittest.main()
