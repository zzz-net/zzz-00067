from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 1. 真实 CLI 帮助输出获取（通过 subprocess，处理编码）
# ---------------------------------------------------------------------------

HELP_ENCODINGS = ["utf-8", "gbk", "gb2312", "cp936", "mbcs", "latin-1"]


def _decode_bytes(raw: bytes) -> str:
    """尝试多种编码解码字节，优先 UTF-8，处理中文乱码问题。"""
    last_error: Optional[Exception] = None
    for enc in HELP_ENCODINGS:
        try:
            text = raw.decode(enc)
            return text
        except (UnicodeDecodeError, LookupError) as e:
            last_error = e
            continue
    if last_error:
        raise last_error
    return raw.decode("utf-8", errors="replace")


def run_cli_help(args: List[str], timeout: int = 30) -> str:
    """
    运行真实 CLI 命令并获取帮助输出。

    通过 subprocess 调用，捕获原始字节后尝试多种编码解码，
    确保中文帮助文本能被正确读取，不依赖 Click 内部对象。

    Args:
        args: 命令参数列表，如 ["--help"] 或 ["rules", "--help"]
        timeout: 超时秒数

    Returns:
        解码后的帮助文本
    """
    cmd = [sys.executable, "-m", "patrol_archiver.cli"] + args
    result = subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
    )
    stdout = _decode_bytes(result.stdout)
    if result.returncode != 0:
        stderr = _decode_bytes(result.stderr)
        raise RuntimeError(
            f"CLI 命令失败 (退出码 {result.returncode}):\n"
            f"命令: {' '.join(cmd)}\n"
            f"stdout: {stdout}\n"
            f"stderr: {stderr}"
        )
    return stdout


# ---------------------------------------------------------------------------
# 2. 帮助文本解析（从真实 --help 输出中提取命令结构）
# ---------------------------------------------------------------------------


@dataclass
class HelpCommandInfo:
    """从真实帮助输出中解析出的命令信息。"""

    name: str
    path: List[str]
    help_short: str
    is_group: bool

    @property
    def full_name(self) -> str:
        return " ".join(self.path)


def _parse_commands_section(help_text: str) -> List[Tuple[str, str]]:
    """
    从帮助文本中提取 Commands: 段落的命令列表。

    返回 [(命令名, 简短帮助), ...]，按名称排序。
    """
    lines = help_text.splitlines()
    in_commands = False
    commands: List[Tuple[str, str]] = []

    for line in lines:
        stripped = line.rstrip()
        if stripped.strip() == "Commands:":
            in_commands = True
            continue
        if in_commands:
            if not stripped.strip():
                if commands:
                    break
                continue
            if stripped.startswith("  ") and not stripped.startswith("   "):
                content = stripped.strip()
                if not content:
                    continue
                parts = content.split(None, 1)
                if len(parts) == 2:
                    cmd_name, cmd_help = parts
                else:
                    cmd_name = parts[0]
                    cmd_help = ""
                commands.append((cmd_name, cmd_help))
            elif commands and stripped.startswith("Options:"):
                break
            elif commands and not stripped.startswith(" "):
                break

    return sorted(commands, key=lambda x: x[0])


def _is_group_command(help_text: str) -> bool:
    """判断一条命令是否为 group（即包含子命令）。"""
    return "Commands:" in help_text


def collect_real_help_commands(
    base_path: Optional[List[str]] = None,
) -> List[HelpCommandInfo]:
    """
    递归调用真实 CLI --help，收集所有命令及其简短帮助文本。

    完全基于真实 subprocess 输出，不使用 Click 内部对象。

    Args:
        base_path: 当前递归的命令路径，如 ["rules"]

    Returns:
        排序后的命令信息列表
    """
    base_path = base_path or []
    args = base_path + ["--help"]
    help_text = run_cli_help(args)

    commands = _parse_commands_section(help_text)
    result: List[HelpCommandInfo] = []

    for cmd_name, cmd_help in commands:
        cmd_path = base_path + [cmd_name]
        sub_args = cmd_path + ["--help"]
        try:
            sub_help = run_cli_help(sub_args)
            is_group = _is_group_command(sub_help)
        except Exception:
            is_group = False

        info = HelpCommandInfo(
            name=cmd_name,
            path=cmd_path,
            help_short=cmd_help,
            is_group=is_group,
        )
        result.append(info)

        if is_group:
            sub_commands = collect_real_help_commands(cmd_path)
            result.extend(sub_commands)

    return result


# ---------------------------------------------------------------------------
# 3. README "命令参考"章节解析
# ---------------------------------------------------------------------------


@dataclass
class ReadmeCommandEntry:
    """README 命令参考中解析出的一条条目。"""

    command_name: str
    parent: Optional[str]
    help_text: str
    indent: int
    readme_line: int
    raw_line: str

    @property
    def path(self) -> List[str]:
        if self.parent:
            return [self.parent, self.command_name]
        return [self.command_name]

    @property
    def full_name(self) -> str:
        return " ".join(self.path)


README_COMMAND_REF_HEADER = "## 命令参考"


def extract_command_ref_block(readme_text: str) -> Tuple[str, int, int]:
    """
    从 README 中提取命令参考代码块内容。

    Returns:
        (block_content, block_start_line, block_end_line)
        行号均为 1-based（含）。
    """
    lines = readme_text.splitlines()
    header_idx: Optional[int] = None
    for i, line in enumerate(lines):
        if line.strip() == README_COMMAND_REF_HEADER:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"README 中未找到章节: {README_COMMAND_REF_HEADER}")

    code_start: Optional[int] = None
    code_end: Optional[int] = None
    for j in range(header_idx + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped.startswith("```") and code_start is None:
            code_start = j
            continue
        if code_start is not None and stripped == "```" and j > code_start:
            code_end = j
            break

    if code_start is None or code_end is None:
        raise ValueError("README 命令参考章节中未找到代码块围栏 ```")

    block_lines = lines[code_start + 1 : code_end]
    return "\n".join(block_lines), code_start + 2, code_end


def parse_readme_command_reference(
    readme_text: str,
) -> Tuple[List[ReadmeCommandEntry], int, int]:
    """
    解析 README 命令参考章节，返回条目列表及代码块行号范围。

    解析规则：
    - 顶层条目：以 2 空格缩进开头，命令名紧跟帮助文本
    - 子命令条目：以 4 空格缩进开头，归属最近的顶层 GROUP 命令
    - 空行忽略
    - 非命令行（如 "patrol [OPTIONS]..."、"Commands:"）忽略
    """
    block_content, block_start_line, block_end_line = extract_command_ref_block(
        readme_text
    )
    entries: List[ReadmeCommandEntry] = []
    current_parent: Optional[str] = None

    for offset, raw in enumerate(block_content.splitlines()):
        line_no = block_start_line + offset
        stripped = raw.rstrip()
        if not stripped.strip():
            continue
        if stripped.strip().startswith("patrol "):
            continue
        if stripped.strip() == "Commands:":
            continue

        leading_spaces = len(stripped) - len(stripped.lstrip())
        content = stripped.strip()

        if leading_spaces >= 2 and leading_spaces < 4:
            parts = content.split(None, 1)
            if len(parts) == 0:
                continue
            cmd = parts[0]
            help_txt = parts[1] if len(parts) > 1 else ""
            entries.append(
                ReadmeCommandEntry(
                    command_name=cmd,
                    parent=None,
                    help_text=help_txt,
                    indent=leading_spaces,
                    readme_line=line_no,
                    raw_line=raw,
                )
            )
            current_parent = cmd
        elif leading_spaces >= 4:
            parts = content.split(None, 1)
            if len(parts) == 0:
                continue
            cmd = parts[0]
            help_txt = parts[1] if len(parts) > 1 else ""
            entries.append(
                ReadmeCommandEntry(
                    command_name=cmd,
                    parent=current_parent,
                    help_text=help_txt,
                    indent=leading_spaces,
                    readme_line=line_no,
                    raw_line=raw,
                )
            )

    return entries, block_start_line, block_end_line


# ---------------------------------------------------------------------------
# 4. 差异比对报告
# ---------------------------------------------------------------------------


@dataclass
class CommandDiff:
    """单个命令层面的差异。"""

    kind: str
    full_name: str
    message: str
    cli_help: str = ""
    readme_help: str = ""
    readme_line: Optional[int] = None


@dataclass
class QualityCheckReport:
    """README 命令参考质量检查完整报告。"""

    missing_in_readme: List[CommandDiff] = field(default_factory=list)
    extra_in_readme: List[CommandDiff] = field(default_factory=list)
    help_mismatch: List[CommandDiff] = field(default_factory=list)
    section_missing: List[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(
            self.missing_in_readme
            or self.extra_in_readme
            or self.help_mismatch
            or self.section_missing
        )

    @property
    def issue_count(self) -> int:
        return (
            len(self.missing_in_readme)
            + len(self.extra_in_readme)
            + len(self.help_mismatch)
            + len(self.section_missing)
        )

    def format_report(self) -> str:
        """格式化输出差异报告，便于定位修改。"""
        if not self.has_issues:
            return "✓ README 命令参考与 CLI 完全一致"

        parts: List[str] = ["README 命令参考质量检查发现以下问题："]

        if self.section_missing:
            parts.append("")
            parts.append("## 缺失章节")
            for s in self.section_missing:
                parts.append(f"  - {s}")

        if self.missing_in_readme:
            parts.append("")
            parts.append("## README 缺失的命令")
            for d in self.missing_in_readme:
                parts.append(f"  - [{d.full_name}] {d.message}")
                if d.cli_help:
                    parts.append(f"    CLI 帮助: {d.cli_help}")

        if self.extra_in_readme:
            parts.append("")
            parts.append("## README 多出的命令（CLI 中不存在）")
            for d in self.extra_in_readme:
                line_info = f"README.md#L{d.readme_line}" if d.readme_line else "位置未知"
                parts.append(f"  - [{d.full_name}] {line_info} - {d.message}")
                if d.readme_help:
                    parts.append(f"    README 文案: {d.readme_help}")

        if self.help_mismatch:
            parts.append("")
            parts.append("## 帮助文案不一致")
            for d in self.help_mismatch:
                line_info = f"README.md#L{d.readme_line}" if d.readme_line else "位置未知"
                parts.append(f"  - [{d.full_name}] {line_info}")
                parts.append(f"    CLI:    {d.cli_help}")
                parts.append(f"    README: {d.readme_help}")

        return "\n".join(parts)


# ---------------------------------------------------------------------------
# 5. 差异比对核心逻辑
# ---------------------------------------------------------------------------


def compare_cli_and_readme(
    cli_commands: List[HelpCommandInfo],
    readme_entries: List[ReadmeCommandEntry],
    help_strict: bool = True,
) -> QualityCheckReport:
    """
    比对 CLI 真实命令树与 README 命令参考条目。

    Args:
        cli_commands: 从真实帮助输出解析出的命令列表
        readme_entries: 从 README 解析出的命令条目列表
        help_strict: 是否严格比对帮助文案

    Returns:
        质量检查报告（不含 section_missing）
    """
    report = QualityCheckReport()

    cli_by_path = {" ".join(c.path): c for c in cli_commands}
    readme_by_path = {" ".join(e.path): e for e in readme_entries}

    for path, info in cli_by_path.items():
        if path not in readme_by_path:
            report.missing_in_readme.append(
                CommandDiff(
                    kind="missing_in_readme",
                    full_name=path,
                    message="CLI 存在但 README 命令参考中未列出",
                    cli_help=info.help_short,
                )
            )

    for path, entry in readme_by_path.items():
        if path not in cli_by_path:
            report.extra_in_readme.append(
                CommandDiff(
                    kind="extra_in_readme",
                    full_name=path,
                    message="README 列出了但 CLI 中不存在",
                    readme_help=entry.help_text,
                    readme_line=entry.readme_line,
                )
            )

    if help_strict:
        for path, entry in readme_by_path.items():
            if path in cli_by_path:
                cli_help = cli_by_path[path].help_short
                if cli_help and entry.help_text and cli_help != entry.help_text:
                    report.help_mismatch.append(
                        CommandDiff(
                            kind="help_mismatch",
                            full_name=path,
                            message="帮助文本不一致",
                            cli_help=cli_help,
                            readme_help=entry.help_text,
                            readme_line=entry.readme_line,
                        )
                    )

    return report


# ---------------------------------------------------------------------------
# 6. 完整质量检查入口
# ---------------------------------------------------------------------------


def check_readme_command_reference(
    readme_path: Optional[Path] = None,
    help_strict: bool = True,
) -> QualityCheckReport:
    """
    检查 README 命令参考与真实 CLI 帮助输出的一致性。

    核心特点：
    - 读取真实 `python -m patrol_archiver.cli --help` 输出（subprocess）
    - 严格比对帮助文案（默认）
    - 处理中文编码问题
    - 给出精确定位（行号、命令路径）
    - 结果稳定可重复

    Args:
        readme_path: README 文件路径，默认项目根目录下的 README.md
        help_strict: 是否严格比对帮助文案，默认 True

    Returns:
        质量检查报告
    """
    if readme_path is None:
        readme_path = Path(__file__).resolve().parent.parent / "README.md"

    report = QualityCheckReport()

    try:
        readme_text = readme_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        report.section_missing.append(f"README 文件不存在: {readme_path}")
        return report

    if README_COMMAND_REF_HEADER not in readme_text:
        report.section_missing.append(
            f"缺少章节: {README_COMMAND_REF_HEADER}"
        )
        return report

    try:
        readme_entries, _, _ = parse_readme_command_reference(readme_text)
    except ValueError as e:
        report.section_missing.append(str(e))
        return report

    try:
        cli_commands = collect_real_help_commands()
    except Exception as e:
        report.section_missing.append(f"获取 CLI 帮助输出失败: {e}")
        return report

    compare_result = compare_cli_and_readme(
        cli_commands, readme_entries, help_strict=help_strict
    )
    report.missing_in_readme = compare_result.missing_in_readme
    report.extra_in_readme = compare_result.extra_in_readme
    report.help_mismatch = compare_result.help_mismatch

    return report


# ---------------------------------------------------------------------------
# 6. README 命令参考同步 / 重建工具
# ---------------------------------------------------------------------------


def _group_commands_by_parent(
    commands: List[HelpCommandInfo],
) -> Dict[Optional[str], List[HelpCommandInfo]]:
    """按父命令分组，便于生成层次化输出。"""
    groups: Dict[Optional[str], List[HelpCommandInfo]] = {}
    for cmd in commands:
        parent = " ".join(cmd.path[:-1]) if len(cmd.path) > 1 else None
        groups.setdefault(parent, []).append(cmd)
    for key in groups:
        groups[key].sort(key=lambda c: c.name)
    return groups


def _calc_column_width(entries: List[HelpCommandInfo]) -> int:
    """计算命令名列的对齐宽度（最长命令名 + 2 空格）。"""
    if not entries:
        return 20
    max_len = max(len(e.name) for e in entries)
    return max_len + 2


def generate_command_reference_block(
    cli_commands: Optional[List[HelpCommandInfo]] = None,
) -> str:
    """
    根据真实 CLI 帮助输出生成 README 命令参考代码块内容。

    格式与 `python -m patrol_archiver.cli --help` 输出保持一致：
    - 顶层命令：2 空格缩进，命令名 + 对齐的帮助文本
    - 子命令：4 空格缩进，按父命令分组排列
    - 组之间空一行

    Args:
        cli_commands: 可选，传入预收集的命令列表以避免重复 subprocess 调用

    Returns:
        代码块内容（不含 ``` 围栏）
    """
    if cli_commands is None:
        cli_commands = collect_real_help_commands()

    grouped = _group_commands_by_parent(cli_commands)
    top_level = grouped.get(None, [])

    lines: List[str] = []
    lines.append("patrol [OPTIONS] COMMAND [ARGS]...")
    lines.append("")
    lines.append("Commands:")

    for i, group_cmd in enumerate(top_level):
        col_width = _calc_column_width([group_cmd])
        indent = "  "
        cmd_name = group_cmd.name.ljust(col_width)
        lines.append(f"{indent}{cmd_name}{group_cmd.help_short}")

        if group_cmd.is_group:
            sub_cmds = grouped.get(group_cmd.full_name, [])
            if sub_cmds:
                sub_col_width = _calc_column_width(sub_cmds)
                sub_indent = "    "
                for sub in sub_cmds:
                    sub_name = sub.name.ljust(sub_col_width)
                    lines.append(f"{sub_indent}{sub_name}{sub.help_short}")

        # 组之间空一行（除了最后一个）
        if i < len(top_level) - 1:
            lines.append("")

    return "\n".join(lines)


def sync_readme_command_reference(
    readme_path: Optional[Path] = None,
    cli_commands: Optional[List[HelpCommandInfo]] = None,
) -> Tuple[bool, int, int, QualityCheckReport]:
    """
    同步 README 命令参考章节：用真实 CLI 帮助输出重写代码块。

    流程：
    1. 收集真实 CLI 命令结构
    2. 生成格式正确的命令参考代码块
    3. 定位 README 中 `## 命令参考` 章节的代码块
    4. 替换旧代码块为新生成的内容
    5. 写入文件并返回新报告

    Args:
        readme_path: README 文件路径，默认项目根目录 README.md
        cli_commands: 可选，预收集的 CLI 命令列表

    Returns:
        (changed, old_issue_count, new_issue_count, new_report)
        - changed: 是否实际修改了文件
        - old_issue_count: 同步前的问题数
        - new_issue_count: 同步后的问题数（应为 0）
        - new_report: 同步后的质量检查报告
    """
    if readme_path is None:
        readme_path = Path(__file__).resolve().parent.parent / "README.md"

    old_report = check_readme_command_reference(
        readme_path=readme_path, help_strict=True
    )
    old_issue_count = old_report.issue_count

    readme_text = readme_path.read_text(encoding="utf-8")
    lines = readme_text.splitlines(keepends=True)

    header_idx: Optional[int] = None
    for i, line in enumerate(lines):
        if line.strip() == README_COMMAND_REF_HEADER:
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"README 中未找到章节: {README_COMMAND_REF_HEADER}")

    code_start: Optional[int] = None
    code_end: Optional[int] = None
    for j in range(header_idx + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped.startswith("```") and code_start is None:
            code_start = j
            continue
        if code_start is not None and stripped == "```" and j > code_start:
            code_end = j
            break

    if code_start is None or code_end is None:
        raise ValueError("README 命令参考章节中未找到代码块围栏 ```")

    new_block_content = generate_command_reference_block(cli_commands=cli_commands)

    fence_line = lines[code_start]
    # 保留原围栏行的 ``` 和语言标记（如果有）
    new_lines = (
        lines[: code_start + 1]
        + [new_block_content + "\n" if not new_block_content.endswith("\n") else new_block_content]
        + [lines[code_end]]
        + lines[code_end + 1 :]
    )

    new_readme_text = "".join(new_lines)
    changed = new_readme_text != readme_text

    if changed:
        readme_path.write_text(new_readme_text, encoding="utf-8")

    new_report = check_readme_command_reference(
        readme_path=readme_path, help_strict=True
    )
    new_issue_count = new_report.issue_count

    return changed, old_issue_count, new_issue_count, new_report


# ---------------------------------------------------------------------------
# 7. CLI 入口（可选，便于直接运行）
# ---------------------------------------------------------------------------


def _setup_output_encoding():
    """配置标准输出编码，确保中文和特殊字符能正确显示。"""
    import os

    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def main():
    """命令行入口：python -m patrol_archiver.readme_quality_check"""
    import argparse

    _setup_output_encoding()

    parser = argparse.ArgumentParser(
        description="检查 / 同步 README 命令参考与 CLI 帮助输出的一致性"
    )
    parser.add_argument(
        "--readme",
        type=Path,
        default=None,
        help="README 文件路径",
    )
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="不严格比对帮助文案（只检查命令存在性）",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="同步模式：用真实 CLI 帮助输出重写 README 命令参考章节",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="生成模式：只打印新的命令参考代码块，不修改文件",
    )
    args = parser.parse_args()

    if args.generate:
        block = generate_command_reference_block()
        print("```")
        print(block)
        print("```")
        sys.exit(0)

    if args.sync:
        try:
            changed, old_count, new_count, new_report = sync_readme_command_reference(
                readme_path=args.readme,
            )
        except ValueError as e:
            print(f"同步失败: {e}")
            sys.exit(2)

        if changed:
            print(f"已同步 README 命令参考章节")
            print(f"  同步前问题数: {old_count}")
            print(f"  同步后问题数: {new_count}")
            if new_count > 0:
                print()
                print(new_report.format_report())
                sys.exit(1)
            else:
                print("  同步后严格校验通过 ✓")
                sys.exit(0)
        else:
            print("README 命令参考已是最新，无需修改")
            if new_count > 0:
                print(new_report.format_report())
                sys.exit(1)
            else:
                print("严格校验通过 ✓")
                sys.exit(0)

    report = check_readme_command_reference(
        readme_path=args.readme,
        help_strict=not args.no_strict,
    )

    print(report.format_report())

    if report.has_issues:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
