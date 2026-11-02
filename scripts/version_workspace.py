#!/usr/bin/env python3
"""Create, inspect, and verify immutable PRD test-design package versions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional


VERSION_RE = re.compile(r"^(?P<day>\d{8})-v(?P<seq>\d{2,})$")
REQUIREMENT_FILE = "00-需求文档.md"
STANDARD_PACKAGE_FILES = (
    REQUIREMENT_FILE,
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
SEQUENCE_PACKAGE_FILES = (
    REQUIREMENT_FILE,
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
STANDARD_TO_SEQUENCE = {
    "06-测试场景.md": "07-测试场景.md",
    "07-测试用例.md": "08-测试用例.md",
    "08-高价值用例筛选.md": "09-高价值用例筛选.md",
    "09-用例评审.md": "10-用例评审.md",
}


class VersionWorkspaceError(RuntimeError):
    pass


def _validate_day(value: str) -> str:
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise VersionWorkspaceError(f"无效日期版本: {value}") from exc
    return value


def _format_seq(value: int) -> str:
    if value < 1:
        raise VersionWorkspaceError("版本序号必须从 01 开始")
    return f"{value:02d}"


def _validate_requirement_name(value: str) -> str:
    name = value.strip()
    if not name or name in {".", ".."} or any(part in name for part in ("/", "\\")):
        raise VersionWorkspaceError("需求名不能为空，且不能包含路径分隔符")
    return name


@dataclass(frozen=True, order=True)
class PackageVersion:
    day: str
    seq: int

    @property
    def id(self) -> str:
        return f"{self.day}-v{_format_seq(self.seq)}"

    @classmethod
    def parse(cls, value: str) -> "PackageVersion":
        match = VERSION_RE.fullmatch(value)
        if not match:
            raise VersionWorkspaceError(f"无效包版本 ID: {value}，应为 YYYYMMDD-vNN")
        seq = int(match.group("seq"))
        if seq < 1:
            raise VersionWorkspaceError(f"版本序号必须大于 0: {value}")
        return cls(_validate_day(match.group("day")), seq)


@dataclass
class Workspace:
    root: Path
    requirement_name: str

    @property
    def requirement_root(self) -> Path:
        return self.root / self.requirement_name

    def ensure_root(self) -> None:
        self.requirement_root.mkdir(parents=True, exist_ok=True)

    def versions(self) -> list[PackageVersion]:
        return sorted(_scan_versions(self.requirement_root, PackageVersion.parse))

    def latest_version(self) -> Optional[PackageVersion]:
        versions = self.versions()
        return versions[-1] if versions else None

    def version_path(self, version: PackageVersion) -> Path:
        return self.requirement_root / version.id


def _scan_versions(directory: Path, parser) -> Iterable:
    if not directory.exists():
        return []
    versions = []
    for child in directory.iterdir():
        if not child.is_dir():
            continue
        try:
            versions.append(parser(child.name))
        except VersionWorkspaceError:
            continue
    return versions


def _absolute(path: Optional[Path]) -> Optional[str]:
    return str(path.resolve()) if path is not None else None


def _package_layout(path: Path) -> str:
    return "sequence" if (path / "06-时序图.md").is_file() else "standard"


def _required_package_files(layout: str) -> tuple[str, ...]:
    return SEQUENCE_PACKAGE_FILES if layout == "sequence" else STANDARD_PACKAGE_FILES


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_manifest(root: Path) -> dict[str, tuple[str, Optional[str]]]:
    manifest: dict[str, tuple[str, Optional[str]]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            manifest[relative] = ("dir", None)
        elif path.is_file():
            manifest[relative] = ("file", _file_sha256(path))
        else:
            raise VersionWorkspaceError(f"版本目录包含不支持的条目: {path}")
    return manifest


def _copy_directory_atomic(source: Path, target: Path) -> int:
    if target.exists():
        raise VersionWorkspaceError(f"目标版本已存在，禁止覆盖: {target}")
    source_manifest = _tree_manifest(source)
    temporary = target.parent / f".{target.name}.tmp-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source, temporary, copy_function=shutil.copy2)
        if _tree_manifest(temporary) != source_manifest:
            raise VersionWorkspaceError("完整复制校验失败：目标临时目录与来源目录不一致")
        os.replace(temporary, target)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return len(source_manifest)


def _create_empty_directory(target: Path) -> None:
    try:
        target.mkdir()
    except FileExistsError as exc:
        raise VersionWorkspaceError(f"目标版本已存在，禁止覆盖: {target}") from exc


def _resolve_today(value: Optional[str]) -> str:
    return _validate_day(value) if value else date.today().strftime("%Y%m%d")


def _next_version(versions: list[PackageVersion], today: str) -> PackageVersion:
    latest = versions[-1] if versions else None
    effective_day = max(today, latest.day) if latest else today
    next_seq = max((item.seq for item in versions if item.day == effective_day), default=0) + 1
    return PackageVersion(effective_day, next_seq)


def command_inspect(workspace: Workspace) -> dict:
    latest = workspace.latest_version()
    selected_path = workspace.version_path(latest) if latest else None
    return {
        "operation": "inspect",
        "root": _absolute(workspace.root),
        "requirement_name": workspace.requirement_name,
        "requirement_root": _absolute(workspace.requirement_root),
        "selected_version_id": latest.id if latest else None,
        "selected_version_path": _absolute(selected_path),
        "selected_requirement_path": _absolute(selected_path / REQUIREMENT_FILE) if selected_path else None,
        "selected_layout": _package_layout(selected_path) if selected_path else None,
        "read_policy": "只允许读取以上选中的最新版本内容；其他版本仅可读取目录名称。",
    }


def command_new_version(workspace: Workspace, args: argparse.Namespace) -> dict:
    workspace.ensure_root()
    versions = workspace.versions()
    latest = versions[-1] if versions else None
    operation_day = _resolve_today(args.today)
    target = _next_version(versions, operation_day)
    target_path = workspace.version_path(target)
    source_path: Optional[Path] = None
    source_requirement_path: Optional[Path] = None
    copied_entry_count = 0

    if args.source_requirement is not None and args.mode != "generate":
        raise VersionWorkspaceError("--source-requirement 仅适用于 --mode generate")

    if args.mode == "create":
        if latest is not None:
            raise VersionWorkspaceError("该需求名已有版本；新需求请使用新的需求名，补充请使用 --mode supplement")
        _create_empty_directory(target_path)
        required_follow_up = f"在 {REQUIREMENT_FILE} 写入需求文档，再按用户确认的布局生成后续文件。"
        read_policy = "空白新需求：不读取历史正文。"
    elif args.mode == "generate":
        if args.source_requirement is not None:
            if latest is not None:
                raise VersionWorkspaceError("已有需求版本时不能使用 --source-requirement；请从最新版本生成或使用 --mode supplement")
            source_requirement = args.source_requirement.expanduser().resolve()
            if not source_requirement.is_file():
                raise VersionWorkspaceError(f"外部需求文档不存在: {source_requirement}")
            source_requirement_path = source_requirement
            _create_empty_directory(target_path)
            shutil.copy2(source_requirement, target_path / REQUIREMENT_FILE)
            copied_entry_count = 1
            required_follow_up = "只基于已复制的外部 PRD，按用户确认的布局生成后续文件。"
            read_policy = "首次独立生成：只读取指定的外部 PRD，不读取历史测试产物。"
        else:
            if latest is None:
                raise VersionWorkspaceError("没有可用需求版本；首次生成请传入 --source-requirement <正式PRD路径>")
            source_path = workspace.version_path(latest)
            source_requirement = source_path / REQUIREMENT_FILE
            if not source_requirement.is_file():
                raise VersionWorkspaceError(f"来源需求文档不存在: {source_requirement}")
            source_requirement_path = source_requirement
            _create_empty_directory(target_path)
            shutil.copy2(source_requirement, target_path / REQUIREMENT_FILE)
            copied_entry_count = 1
            required_follow_up = "只基于已复制的 00-需求文档.md，按用户确认的布局生成后续文件。"
            read_policy = "独立生成：只读取来源版本的 00-需求文档.md，不读取任何历史测试产物。"
    else:
        if latest is None:
            raise VersionWorkspaceError("没有可补充的版本；请先创建并完成一个完整需求包")
        source_path = workspace.version_path(latest)
        copied_entry_count = _copy_directory_atomic(source_path, target_path)
        required_follow_up = "在完整副本上叠加修改，完成后运行 verify-supplement。"
        read_policy = "补充：只读取来源完整包；来源包已完整复制到 created_path。"

    return {
        "operation": f"new-version-{args.mode}",
        "root": _absolute(workspace.root),
        "requirement_name": workspace.requirement_name,
        "source_version_id": latest.id if source_path else None,
        "source_version_path": _absolute(source_path),
        "source_requirement_path": _absolute(source_requirement_path),
        "created_id": target.id,
        "created_path": _absolute(target_path),
        "requirement_path": _absolute(target_path / REQUIREMENT_FILE),
        "layout": _package_layout(target_path),
        "copied_entry_count": copied_entry_count,
        "read_policy": read_policy,
        "write_policy": "只允许写入 created_path；来源和其他版本只读。",
        "required_follow_up": required_follow_up,
    }


def _entry_kinds(root: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        entries[relative] = "dir" if path.is_dir() else "file" if path.is_file() else "unsupported"
    return entries


def command_verify_supplement(workspace: Workspace, args: argparse.Namespace) -> dict:
    source = PackageVersion.parse(args.source)
    target = PackageVersion.parse(args.target)
    if target <= source:
        raise VersionWorkspaceError("补充目标版本必须晚于来源版本")
    source_path = workspace.version_path(source)
    target_path = workspace.version_path(target)
    if not source_path.is_dir() or not target_path.is_dir():
        raise VersionWorkspaceError("补充来源或目标目录不存在")

    source_entries = _entry_kinds(source_path)
    target_entries = _entry_kinds(target_path)
    source_layout = _package_layout(source_path)
    target_layout = _package_layout(target_path)
    if source_layout == "sequence" and target_layout == "standard":
        raise VersionWorkspaceError("时序图布局禁止降级为标准布局")
    entry_mapping = STANDARD_TO_SEQUENCE if source_layout == "standard" and target_layout == "sequence" else {}
    missing_entries = sorted(
        key
        for key, kind in source_entries.items()
        if target_entries.get(entry_mapping.get(key, key)) != kind
    )
    required_files = _required_package_files(target_layout)
    missing_required = sorted(name for name in required_files if not (target_path / name).is_file())
    if missing_entries or missing_required:
        details = []
        if missing_entries:
            details.append("缺少来源条目: " + ", ".join(missing_entries))
        if missing_required:
            details.append("缺少必需文件: " + ", ".join(missing_required))
        raise VersionWorkspaceError("补充版本不是自包含目录；" + "；".join(details))
    return {
        "operation": "verify-supplement",
        "source_id": source.id,
        "source_path": _absolute(source_path),
        "target_id": target.id,
        "target_path": _absolute(target_path),
        "source_entry_count": len(source_entries),
        "target_entry_count": len(target_entries),
        "source_layout": source_layout,
        "target_layout": target_layout,
        "self_contained": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="管理按需求名归档的不可变 PRD 测试设计包")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_workspace_args(target: argparse.ArgumentParser) -> None:
        target.add_argument("--root", required=True, type=Path)
        target.add_argument("--requirement-name", required=True, help="外层需求目录名称")

    inspect_parser = subparsers.add_parser("inspect", help="返回指定需求的最新版本")
    add_workspace_args(inspect_parser)

    create_parser = subparsers.add_parser("new-version", help="创建新的不可变需求包版本")
    add_workspace_args(create_parser)
    create_parser.add_argument("--mode", required=True, choices=("create", "generate", "supplement"))
    create_parser.add_argument(
        "--source-requirement",
        type=Path,
        help="首次 generate 时指定包外正式 PRD；与已有版本或其他 mode 不能同时使用",
    )
    create_parser.add_argument("--today", help="实际操作日期，格式 YYYYMMDD")

    verify_parser = subparsers.add_parser("verify-supplement", help="验证补充包保留来源全部条目并自包含")
    add_workspace_args(verify_parser)
    verify_parser.add_argument("--source", required=True, help="来源版本 ID，格式 YYYYMMDD-vNN")
    verify_parser.add_argument("--target", required=True, help="目标版本 ID，格式 YYYYMMDD-vNN")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        workspace = Workspace(args.root.resolve(), _validate_requirement_name(args.requirement_name))
        if args.command == "inspect":
            result = command_inspect(workspace)
        elif args.command == "new-version":
            result = command_new_version(workspace, args)
        else:
            result = command_verify_supplement(workspace, args)
    except VersionWorkspaceError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
