#!/usr/bin/env python3
"""Migrate a newly created v9 package from standard to sequence layout."""

from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
from pathlib import Path


VERSION_RE = re.compile(r"^\d{8}-v\d{2,}$")
FILE_MAPPING = {
    "06-测试场景.md": "07-测试场景.md",
    "07-测试用例.md": "08-测试用例.md",
    "08-高价值用例筛选.md": "09-高价值用例筛选.md",
    "09-用例评审.md": "10-用例评审.md",
}
LINK_RE = re.compile("|".join(re.escape(name) for name in sorted(FILE_MAPPING, key=len, reverse=True)))


class MigrationError(RuntimeError):
    pass


def validate_target(artifact: Path, expected_created_id: str) -> None:
    if not artifact.is_dir():
        raise MigrationError(f"版本目录不存在: {artifact}")
    if artifact.name != expected_created_id or not VERSION_RE.fullmatch(expected_created_id):
        raise MigrationError("--expected-created-id 必须与 version_workspace.py 返回的 created_id 及目录名一致")
    if (artifact / "06-时序图.md").exists():
        raise MigrationError("该版本已经是时序图布局，拒绝重复迁移")
    for source, target in FILE_MAPPING.items():
        if (artifact / source).exists() and (artifact / target).exists():
            raise MigrationError(f"检测到混合布局，源文件和目标文件同时存在: {source}, {target}")


def rename_layout_files(artifact: Path) -> None:
    staged: list[tuple[Path, Path]] = []
    for source_name, target_name in FILE_MAPPING.items():
        source = artifact / source_name
        if not source.exists():
            continue
        temporary = artifact / f".{source_name}.{uuid.uuid4().hex}.sequence-layout-tmp"
        os.replace(source, temporary)
        staged.append((temporary, artifact / target_name))
    for temporary, target in staged:
        os.replace(temporary, target)


def rewrite_markdown_links(artifact: Path) -> int:
    changed = 0
    for path in artifact.rglob("*.md"):
        content = path.read_text(encoding="utf-8")
        rewritten = LINK_RE.sub(lambda match: FILE_MAPPING[match.group(0)], content)
        if rewritten != content:
            path.write_text(rewritten, encoding="utf-8", newline="")
            changed += 1
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", required=True, type=Path, help="version_workspace.py 返回的 created_path")
    parser.add_argument("--expected-created-id", required=True, help="version_workspace.py 返回的 created_id")
    args = parser.parse_args()
    artifact = args.artifact.resolve()
    try:
        validate_target(artifact, args.expected_created_id)
        rename_layout_files(artifact)
        changed_links = rewrite_markdown_links(artifact)
        (artifact / "06-时序图.md").write_text("# 程序时序图\n", encoding="utf-8", newline="")
    except (MigrationError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"OK: sequence layout enabled; rewritten Markdown files: {changed_links}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
