#!/usr/bin/env python3
"""Validate a self-contained prd-test-design-v9 artifact package."""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit


STANDARD_REQUIRED_FILES = (
    "00-需求文档.md",
    "01-workflow-state.md",
    "02-memory-log.md",
    "03-需求规格.md",
    "04-业务规则.md",
    "05-ITEM-POINT-拆分.md",
    "06-测试场景.md",
    "07-测试用例.md",
    "08-高价值用例筛选.md",
    "09-用例评审.md",
)
SEQUENCE_REQUIRED_FILES = (
    "00-需求文档.md",
    "01-workflow-state.md",
    "02-memory-log.md",
    "03-需求规格.md",
    "04-业务规则.md",
    "05-ITEM-POINT-拆分.md",
    "06-时序图.md",
    "07-测试场景.md",
    "08-测试用例.md",
    "09-高价值用例筛选.md",
    "10-用例评审.md",
)
STANDARD_ONLY_FILES = {
    "06-测试场景.md", "07-测试用例.md", "08-高价值用例筛选.md", "09-用例评审.md",
}
SEQUENCE_ONLY_FILES = {
    "06-时序图.md", "07-测试场景.md", "08-测试用例.md", "09-高价值用例筛选.md", "10-用例评审.md",
}


CASE_HEADING_RE = re.compile(r"^##[ \t]+(?P<id>TC-\d+)[ \t]*$", re.M)
CASE_TITLE_RE = re.compile(r"^\*\*标题\*\*：[ \t]*(?P<title>\S[^\n]*)$", re.M)
TRACE_ID_RE = re.compile(r"(?:SRC|SPEC|RULE|ITEM|POINT|SEQ|SCN|TC|LC|CC)-\d+", re.I)
SEQUENCE_HEADING_RE = re.compile(r"^###\s+(?P<id>SEQ-\d+)\s*$", re.M)
SCENARIO_HEADING_RE = re.compile(r"^###\s+(?P<id>SCN-\d+)\s*$", re.M)
FORBIDDEN_CASE_FIELDS = ("风险等级", "用例类型", "自动化建议")


def markdown_prose(content: str) -> str:
    """Blank fenced code without changing offsets, so examples cannot create targets."""
    lines = []
    fence = None
    for line in content.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence is not None:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = None
            lines.append(re.sub(r"[^\r\n]", " ", line))
        elif marker:
            fence = marker[1]
            lines.append(re.sub(r"[^\r\n]", " ", line))
        else:
            lines.append(line)
    return "".join(lines)


def validate_internal_links(artifact: Path, required_files: tuple[str, ...]) -> list[str]:
    """Validate real heading targets in generated files, including links into PRD copies."""
    root = artifact.resolve()
    errors: list[str] = []
    cache: dict[Path, Counter] = {}

    def headings(path: Path) -> Counter:
        if path not in cache:
            prose = markdown_prose(path.read_text(encoding="utf-8"))
            names = re.findall(r"^ {0,3}#{1,6}[ \t]+(.+?)[ \t]*$", prose, re.M)
            cache[path] = Counter(re.sub(r"[ \t]+#+[ \t]*$", "", name).casefold() for name in names)
        return cache[path]

    for name in required_files:
        # Original PRD content is authoritative, not a generated formatting target.
        if name == "00-需求文档.md":
            continue
        path = root / name
        if not path.is_file():
            continue
        for heading, count in headings(path).items():
            if TRACE_ID_RE.fullmatch(heading) and count > 1:
                errors.append(f"{name} duplicate native heading: {heading}")
        content = markdown_prose(path.read_text(encoding="utf-8"))
        content = re.sub(r"(`+)[^\n]*?\1", "", content)
        for label, target in re.findall(r"(?<!!)\[([^\]\n]+)\]\(([^)\n]+)\)", content):
            target = target.strip().removeprefix("<").removesuffix(">")
            url = urlsplit(target)
            if url.scheme or url.netloc:
                if TRACE_ID_RE.fullmatch(label) or url.scheme == "file":
                    errors.append(f"{name} trace links must stay inside this package: {target}")
                continue
            destination = (path.parent / unquote(url.path)).resolve() if url.path else path
            if not destination.is_relative_to(root):
                errors.append(f"{name} internal link escapes package: {target}")
                continue
            if not destination.is_file():
                errors.append(f"{name} missing link target: {target}")
                continue
            if url.fragment:
                fragment = unquote(url.fragment).casefold()
                if destination.suffix.lower() != ".md" or headings(destination)[fragment] != 1:
                    errors.append(f"{name} target needs one real Markdown heading (HTML anchors do not count): {target}")
                if TRACE_ID_RE.fullmatch(label) and fragment != label.casefold():
                    errors.append(f"{name} trace label and target ID differ: {label} -> {target}")
            elif TRACE_ID_RE.fullmatch(label):
                errors.append(f"{name} trace link missing heading fragment: {target}")
    return errors


def package_layout(artifact: Path) -> tuple[str, tuple[str, ...], str]:
    if (artifact / "06-时序图.md").is_file():
        return "sequence", SEQUENCE_REQUIRED_FILES, "08-测试用例.md"
    return "standard", STANDARD_REQUIRED_FILES, "07-测试用例.md"


def validate_sequence_diagrams(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8")
    errors: list[str] = []
    if not re.search(r"^#\s+程序时序图\s*$", content, re.M):
        errors.append("06-时序图.md missing '# 程序时序图' heading")
    headings = list(SEQUENCE_HEADING_RE.finditer(content))
    if not headings:
        return errors + ["06-时序图.md has no SEQ-* headings"]
    for index, heading in enumerate(headings):
        sequence_id = heading.group("id")
        end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        block = content[heading.start():end]
        mermaid = re.search(r"```mermaid\s*\r?\n(?P<body>.*?)```", block, re.S)
        if mermaid is None or not re.search(r"^sequenceDiagram\s*$", mermaid.group("body"), re.M):
            errors.append(f"{sequence_id} missing Mermaid sequenceDiagram")
            continue
        if not re.search(
            r"\[(?:SPEC|RULE|POINT)-\d+\]\((?:03-需求规格|04-业务规则|05-ITEM-POINT-拆分)\.md#(?:spec|rule|point)-\d+\)",
            block,
        ):
            errors.append(f"{sequence_id} missing formal source link")
        diagram = mermaid.group("body")
        if "->>" not in diagram or "-->>" not in diagram:
            errors.append(f"{sequence_id} must express a request and response")
        if not re.search(r"写入|更新|保存|删除|撤销|回滚|状态不变|不写入|不涉及状态写入", block):
            errors.append(f"{sequence_id} missing state-write or explicit no-write semantics")
        if not re.search(r"\belse\b|失败|异常|拒绝|超时|回滚", block):
            errors.append(f"{sequence_id} missing an applicable failure branch")
    return errors


def markdown_case_blocks(path: Path) -> dict[str, str]:
    content = markdown_prose(path.read_text(encoding="utf-8"))
    headings = list(CASE_HEADING_RE.finditer(content))
    return {
        heading.group("id"): content[
            heading.start():headings[index + 1].start() if index + 1 < len(headings) else len(content)
        ]
        for index, heading in enumerate(headings)
    }


def markdown_case_statuses(path: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for case_id, block in markdown_case_blocks(path).items():
        passed = re.findall(r"^- \[(?P<mark>[ xX])\] 通过\s*$", block, re.M)
        failed = re.findall(r"^- \[(?P<mark>[ xX])\] 不通过\s*$", block, re.M)
        if len(passed) != 1 or len(failed) != 1:
            continue
        passed_checked = passed[0].lower() == "x"
        failed_checked = failed[0].lower() == "x"
        if passed_checked and failed_checked:
            statuses[case_id] = "非法"
        elif passed_checked:
            statuses[case_id] = "通过"
        elif failed_checked:
            statuses[case_id] = "不通过"
        else:
            statuses[case_id] = "未测试"
    return statuses


def markdown_case_ids(path: Path) -> set[str]:
    return set(markdown_case_blocks(path))


def markdown_case_titles(path: Path) -> dict[str, str]:
    titles = {}
    for case_id, block in markdown_case_blocks(path).items():
        title = CASE_TITLE_RE.search(block)
        if title:
            titles[case_id] = title.group("title").strip()
    return titles


def validate_case_layout(path: Path, case_ids: set[str]) -> list[str]:
    content = markdown_prose(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    for line in content.splitlines():
        if re.match(r"^##[ \t]+(?:[☐☑][ \t]*)?TC-\d+", line) and not CASE_HEADING_RE.fullmatch(line):
            errors.append(f"{path.name} use ID-only heading '## TC-XXX' and a separate '**标题**：...' line: {line}")
    if re.search(r'<a\b[^>]*(?:id|name)\s*=\s*[\'\"]tc-\d+', content, re.I):
        errors.append(f"{path.name} must use native TC headings instead of HTML anchors")
    for case_id in case_ids:
        heading = re.search(
            rf"^##[ \t]+{re.escape(case_id)}[ \t]*$", content, re.M
        )
        if heading is None:
            errors.append(f"{case_id} must use ID-only heading '## {case_id}'")
            continue
        start = heading.start()
        next_heading = CASE_HEADING_RE.search(content, heading.end())
        end = next_heading.start() if next_heading else len(content)
        block = content[start:end]
        for field in FORBIDDEN_CASE_FIELDS:
            if re.search(rf"^\|\s*{re.escape(field)}\s*\|", block, re.M):
                errors.append(f"{case_id} must not contain removed field: {field}")
        title_block = content[heading.end():end].lstrip()
        if not CASE_TITLE_RE.match(title_block):
            errors.append(f"{case_id} missing separate business title immediately below its ID heading")
        elif len(CASE_TITLE_RE.findall(block)) != 1:
            errors.append(f"{case_id} must contain exactly one business title")
        required_markers = (
            "| 字段 | 值 |",
            "| 优先级 |",
            "| 追踪关系 |",
            "### 前置条件",
            "### 测试数据",
            "| 名称 | 值 |",
            "### 操作步骤与预期结果",
            "### 测试结果",
        )
        if any(marker not in block for marker in required_markers):
            errors.append(f"{case_id} does not follow the required Markdown case layout")
            continue
        if not re.search(
            r"^\|\s*步骤\s*\|\s*操作\s*\|\s*预期结果\s*\|\s*$",
            block,
            re.M,
        ):
            errors.append(f"{case_id} missing three-column step table")
            continue
        if not re.search(
            r"^\|\s*步骤\s*\|\s*操作\s*\|\s*预期结果\s*\|\s*\r?\n"
            r"\|\s*:?-{2,}:?\s*\|\s*:?-{2,}:?\s*\|\s*:?-{2,}:?\s*\|\s*$",
            block,
            re.M,
        ):
            errors.append(f"{case_id} step table separator must contain exactly three columns")
            continue
        step_matches = list(
            re.finditer(
                r"^\|\s*(?P<step>\d+)\s*\|\s*(?P<action>.*?)\s*\|\s*(?P<expected>.*?)\s*\|\s*$",
                block,
                re.M,
            )
        )
        if not step_matches:
            errors.append(f"{case_id} has no step rows")
            continue
        step_ids = [match.group("step") for match in step_matches]
        if len(step_ids) != len(set(step_ids)):
            errors.append(f"{case_id} has duplicate step ids")
        for match in step_matches:
            step_id = match.group("step")
            if not match.group("action").strip():
                errors.append(f"{case_id} step {step_id} missing 操作")
            if not match.group("expected").strip():
                errors.append(f"{case_id} step {step_id} missing 预期结果")
        result_heading = block.find("### 测试结果")
        step_heading = block.find("### 操作步骤与预期结果")
        if result_heading <= step_heading:
            errors.append(f"{case_id} 测试结果 must appear after the step table")
        passed = re.findall(r"^- \[(?P<mark>[ xX])\] 通过\s*$", block, re.M)
        failed = re.findall(r"^- \[(?P<mark>[ xX])\] 不通过\s*$", block, re.M)
        if len(passed) != 1 or len(failed) != 1:
            errors.append(f"{case_id} must contain exactly one 通过 and one 不通过 task checkbox")
        elif passed[0].lower() == "x" and failed[0].lower() == "x":
            errors.append(f"{case_id} 通过 and 不通过 cannot both be checked")
    return errors


def validate_clickable_traceability(path: Path, layout: str) -> list[str]:
    content = markdown_prose(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    scenario_path = "07-测试场景.md" if layout == "sequence" else "06-测试场景.md"
    case_path = "08-测试用例.md" if layout == "sequence" else "07-测试用例.md"

    scenario_requirements = (
        ("POINT", "05-ITEM-POINT-拆分.md", "point"),
        ("RULE", "04-业务规则.md", "rule"),
        ("SPEC", "03-需求规格.md", "spec"),
    )
    headings = list(SCENARIO_HEADING_RE.finditer(content))
    for index, heading in enumerate(headings):
        case_id = heading.group("id")
        end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        block = content[heading.start():end]
        for prefix, filename, anchor in scenario_requirements:
            pattern = rf"\[{prefix}-\d+\]\({re.escape(filename)}#{anchor}-\d+\)"
            if not re.search(pattern, block):
                errors.append(f"{case_id} missing clickable {prefix} trace link")

    if not headings:
        errors.append(f"{path.name} has no SCN-* headings")

    return errors


def validate_case_traceability(path: Path, layout: str) -> list[str]:
    errors: list[str] = []
    scenario_path = "07-测试场景.md" if layout == "sequence" else "06-测试场景.md"
    requirements = (
        ("ITEM", "05-ITEM-POINT-拆分.md", "item"),
        ("POINT", "05-ITEM-POINT-拆分.md", "point"),
        ("SCN", scenario_path, "scn"),
        ("RULE", "04-业务规则.md", "rule"),
        ("SPEC", "03-需求规格.md", "spec"),
    )
    for case_id, block in markdown_case_blocks(path).items():
        for prefix, filename, anchor in requirements:
            pattern = rf"\[{prefix}-\d+\]\({re.escape(filename)}#{anchor}-\d+\)"
            if not re.search(pattern, block):
                errors.append(f"{case_id} missing clickable {prefix} trace link")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, help="v9 artifact directory")
    args = parser.parse_args()
    artifact = Path(args.artifact)
    errors: list[str] = []

    if not artifact.is_dir():
        print(f"ERROR: artifact directory does not exist: {artifact}")
        return 1

    layout, required_files, master_name = package_layout(artifact)
    for name in required_files:
        if not (artifact / name).is_file():
            errors.append(f"missing required file: {name}")

    conflicting_files = STANDARD_ONLY_FILES if layout == "sequence" else SEQUENCE_ONLY_FILES
    for name in sorted(conflicting_files):
        if name not in required_files and (artifact / name).exists():
            errors.append(f"mixed package layouts: unexpected file {name}")

    if layout == "sequence" and (artifact / "06-时序图.md").is_file():
        errors.extend(validate_sequence_diagrams(artifact / "06-时序图.md"))

    master_path = artifact / master_name
    if master_path.is_file():
        master_ids = markdown_case_ids(master_path)
        if not master_ids:
            errors.append(f"{master_name} has no TC-* headings")
        layout_errors = validate_case_layout(master_path, master_ids)
        errors.extend(layout_errors)
        errors.extend(validate_case_traceability(master_path, layout))

    scenario_path = artifact / ("07-测试场景.md" if layout == "sequence" else "06-测试场景.md")
    if scenario_path.is_file():
        errors.extend(validate_clickable_traceability(scenario_path, layout))

    errors.extend(validate_internal_links(artifact, required_files))

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print(f"OK: v9 {layout} package valid ({len(markdown_case_ids(master_path))} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
