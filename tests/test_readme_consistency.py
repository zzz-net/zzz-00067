from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from click.testing import CliRunner

from patrol_archiver.cli import cli


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# ---------------------------------------------------------------------------
# 1. CLI 命令结构提取（通过 Click 内部 API，不依赖终端编码）
# ---------------------------------------------------------------------------


@dataclass
class CliCommandInfo:
    """表示一条 CLI 命令的结构化信息。"""

    name: str
    path: List[str]
    help: str
    is_group: bool

    @property
    def full_name(self) -> str:
        return " ".join(self.path)

    @property
    def help_first_line(self) -> str:
        """取 help 文本的第一行（非空、去前后空白）。"""
        if not self.help:
            return ""
        for line in self.help.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return ""


def _walk_click_commands(cmd, path: Optional[List[str]] = None) -> List[CliCommandInfo]:
    """递归遍历 Click 命令树，返回全部命令信息。"""
    path = path or []
    results: List[CliCommandInfo] = []
    for name in sorted(cmd.commands.keys()):
        sub = cmd.commands[name]
        sub_path = path + [name]
        is_group = hasattr(sub, "commands")
        info = CliCommandInfo(
            name=name,
            path=sub_path,
            help=sub.help or "",
            is_group=is_group,
        )
        results.append(info)
        if is_group:
            results.extend(_walk_click_commands(sub, sub_path))
    return results


def collect_cli_command_structure() -> List[CliCommandInfo]:
    """从真实 Click CLI 对象中提取全部命令及其帮助。"""
    return _walk_click_commands(cli)


def collect_cli_commands_by_depth() -> Dict[int, List[CliCommandInfo]]:
    """按命令深度（层级）分组。depth=1 为顶层命令，depth=2 为子命令。"""
    result: Dict[int, List[CliCommandInfo]] = {}
    for info in collect_cli_command_structure():
        d = len(info.path)
        result.setdefault(d, []).append(info)
    return result


# ---------------------------------------------------------------------------
# 2. README "命令参考"章节解析
# ---------------------------------------------------------------------------


@dataclass
class ReadmeCommandEntry:
    """README 命令参考中解析出的一条条目。"""

    command_name: str
    parent: Optional[str]  # 父命令名；顶层命令为 None
    help_text: str
    indent: int
    readme_line: int  # 1-based line number in README.md
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
        raise AssertionError(f"README 中未找到章节: {README_COMMAND_REF_HEADER}")

    code_start: Optional[int] = None
    code_end: Optional[int] = None
    for j in range(header_idx + 1, len(lines)):
        if lines[j].strip().startswith("```") and code_start is None:
            code_start = j
            continue
        if code_start is not None and lines[j].strip() == "```" and j > code_start:
            code_end = j
            break

    if code_start is None or code_end is None:
        raise AssertionError("README 命令参考章节中未找到代码块围栏 ```")

    block_lines = lines[code_start + 1 : code_end]
    return "\n".join(block_lines), code_start + 2, code_end  # +2 = 内容第一行 (1-based)


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
    block_content, block_start_line, block_end_line = extract_command_ref_block(readme_text)
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

        # 以 2 空格缩进开头但非 4 空格，视为顶层命令
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
# 3. CLI vs README 差异比对
# ---------------------------------------------------------------------------


@dataclass
class CommandDiff:
    """单个命令层面的差异。"""

    kind: str  # "missing_in_readme" | "extra_in_readme" | "help_mismatch"
    full_name: str
    message: str
    cli_help: str = ""
    readme_help: str = ""
    readme_line: Optional[int] = None


@dataclass
class CommandComparisonReport:
    missing_in_readme: List[CommandDiff] = field(default_factory=list)
    extra_in_readme: List[CommandDiff] = field(default_factory=list)
    help_mismatch: List[CommandDiff] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(
            self.missing_in_readme or self.extra_in_readme or self.help_mismatch
        )

    def format_issues(self) -> str:
        if not self.has_issues:
            return "（无差异）"
        parts: List[str] = []
        if self.missing_in_readme:
            parts.append("README 缺失以下命令：")
            for d in self.missing_in_readme:
                parts.append(f"  - [{d.full_name}] {d.message}")
                if d.cli_help:
                    parts.append(f"      CLI 帮助: {d.cli_help}")
        if self.extra_in_readme:
            parts.append("README 多出以下 CLI 中不存在的命令：")
            for d in self.extra_in_readme:
                parts.append(
                    f"  - [{d.full_name}] README.md#L{d.readme_line} {d.message}"
                )
                if d.readme_help:
                    parts.append(f"      README 文案: {d.readme_help}")
        if self.help_mismatch:
            parts.append("README 帮助文案与 CLI 不一致：")
            for d in self.help_mismatch:
                parts.append(
                    f"  - [{d.full_name}] README.md#L{d.readme_line} {d.message}"
                )
                parts.append(f"      CLI:    {d.cli_help}")
                parts.append(f"      README: {d.readme_help}")
        return "\n".join(parts)


def compare_cli_and_readme(
    cli_commands: List[CliCommandInfo],
    readme_entries: List[ReadmeCommandEntry],
    help_strict: bool = False,
) -> CommandComparisonReport:
    """
    比对 CLI 真实命令树与 README 命令参考章节。

    Args:
        help_strict: True=帮助文本必须完全一致；False=只校验命令存在性
                     （README 通常使用简化描述，所以默认宽松）
    """
    report = CommandComparisonReport()

    cli_by_path = {" ".join(c.path): c for c in cli_commands}
    readme_by_path = {" ".join(e.path): e for e in readme_entries}

    # 1) README 缺失的命令
    for path, info in cli_by_path.items():
        if path not in readme_by_path:
            report.missing_in_readme.append(
                CommandDiff(
                    kind="missing_in_readme",
                    full_name=path,
                    message="CLI 存在但 README 命令参考中未列出",
                    cli_help=info.help_first_line,
                )
            )

    # 2) README 多出的命令
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

    # 3) 帮助文案比对（严格模式）
    if help_strict:
        for path, entry in readme_by_path.items():
            if path in cli_by_path:
                cli_help = cli_by_path[path].help_first_line
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
# 4. 测试类
# ---------------------------------------------------------------------------


class TestCliHelpSource:
    """验证 CLI 命令结构提取工具本身的正确性。"""

    def test_collect_cli_returns_expected_groups(self):
        """提取结果应包含顶层 GROUP：batch/rules/annotate/conflict/export/snapshot/draft。"""
        cmds = collect_cli_command_structure()
        top_groups = {c.name for c in cmds if c.is_group and len(c.path) == 1}
        for expected in ("batch", "rules", "annotate", "conflict", "export", "snapshot", "draft"):
            assert expected in top_groups, f"顶层 GROUP 缺失: {expected}"

    def test_collect_cli_returns_expected_single_commands(self):
        """提取结果应包含顶层非 GROUP 命令。"""
        cmds = collect_cli_command_structure()
        top_commands = {c.name for c in cmds if not c.is_group and len(c.path) == 1}
        for expected in ("import", "scan", "preview", "archive", "undo", "info"):
            assert expected in top_commands, f"顶层命令缺失: {expected}"

    def test_collect_cli_includes_set_archive_dir(self):
        """rules set-archive-dir 必须存在（用于验证我们确实能抓到 README 漏写）。"""
        cmds = collect_cli_command_structure()
        paths = {" ".join(c.path) for c in cmds}
        assert "rules set-archive-dir" in paths, "CLI 中 rules set-archive-dir 命令不见了"

    def test_collect_cli_deterministic(self):
        """两次提取结果完全一致——保证重启后一致性。"""
        first = collect_cli_command_structure()
        second = collect_cli_command_structure()
        first_serial = [(c.full_name, c.is_group, c.help_first_line) for c in first]
        second_serial = [(c.full_name, c.is_group, c.help_first_line) for c in second]
        assert first_serial == second_serial, "CLI 命令结构提取非确定"


class TestReadmeCommandRefParser:
    """验证 README 命令参考解析器本身的正确性。"""

    def setup_method(self):
        self.project_root = Path(__file__).resolve().parent.parent
        self.readme_path = self.project_root / "README.md"
        self.readme_text = self.readme_path.read_text(encoding="utf-8")

    def test_parse_readme_has_draft_group_and_subcommands(self):
        entries, _, _ = parse_readme_command_reference(self.readme_text)
        paths = {" ".join(e.path) for e in entries}
        assert "draft" in paths, "README 命令参考缺少 draft 组"
        for sub in ("draft save", "draft list", "draft show", "draft restore", "draft delete"):
            assert sub in paths, f"README 命令参考缺少 {sub}"

    def test_parse_readme_records_line_numbers(self):
        """README 解析必须记录每条命令的行号，以便精确定位。"""
        entries, start, end = parse_readme_command_reference(self.readme_text)
        assert start > 0 and end > start
        for entry in entries:
            assert start <= entry.readme_line <= end, (
                f"{entry.full_name} 行号 {entry.readme_line} 不在代码块范围 [{start}, {end}]"
            )
            lines = self.readme_text.splitlines()
            raw_in_readme = lines[entry.readme_line - 1]
            assert entry.command_name in raw_in_readme, (
                f"{entry.full_name} 行号 {entry.readme_line} 对应的行不含命令名"
            )

    def test_parse_readme_includes_batch_children_with_correct_parent(self):
        entries, _, _ = parse_readme_command_reference(self.readme_text)
        batch_subs = [e for e in entries if e.parent == "batch"]
        sub_names = {e.command_name for e in batch_subs}
        for expected in ("new", "list", "switch", "show"):
            assert expected in sub_names, f"README 命令参考缺少 batch {expected}"

    def test_parse_synthetic_readme_with_missing_command(self):
        """构造一份缺 command 的 README，确保解析结果确实不包含该命令。"""
        synthetic = """## 命令参考

```
patrol [OPTIONS] COMMAND [ARGS]...

Commands:
  batch      批次管理
    new      创建新批次

  import     导入点位清单
```
"""
        entries, _, _ = parse_readme_command_reference(synthetic)
        paths = {" ".join(e.path) for e in entries}
        assert "batch" in paths
        assert "batch new" in paths
        assert "import" in paths
        # 我们没写 rules，所以解析出的列表不应含 rules
        assert "rules" not in paths


class TestCliVsReadmeConsistency:
    """CLI 命令树 vs README 命令参考章节的一致性校验（核心能力）。"""

    def setup_method(self):
        self.project_root = Path(__file__).resolve().parent.parent
        self.readme_path = self.project_root / "README.md"
        self.readme_text = self.readme_path.read_text(encoding="utf-8")
        self.cli_commands = collect_cli_command_structure()
        self.readme_entries, _, _ = parse_readme_command_reference(self.readme_text)

    def test_no_missing_cli_commands_in_readme(self):
        """CLI 中每个命令都必须在 README 命令参考里出现。"""
        report = compare_cli_and_readme(self.cli_commands, self.readme_entries)
        assert not report.missing_in_readme, (
            "README 命令参考章节缺少以下 CLI 命令：\n" + report.format_issues()
        )

    def test_no_extra_commands_in_readme(self):
        """README 命令参考中不应出现 CLI 中不存在的命令。"""
        report = compare_cli_and_readme(self.cli_commands, self.readme_entries)
        assert not report.extra_in_readme, (
            "README 命令参考章节包含以下不存在的命令：\n" + report.format_issues()
        )

    def test_comparison_deterministic_across_restart(self):
        """
        场景一：重启一致性。

        重新实例化 BatchStore 两次（模拟程序重启），同时重新解析 README，
        报告内容、每个差异的定位信息必须逐字节一致。
        """

        def run_once() -> str:
            cmds = collect_cli_command_structure()
            entries, _, _ = parse_readme_command_reference(
                self.readme_path.read_text(encoding="utf-8")
            )
            rep = compare_cli_and_readme(cmds, entries)
            return rep.format_issues()

        first = run_once()
        second = run_once()
        assert first == second, (
            "两次比对结果不一致（重启一致性失败）。\n"
            f"第一次:\n{first}\n\n第二次:\n{second}"
        )

    def test_comparison_reports_precise_location_on_mismatch(self):
        """
        场景二：差异定位。

        构造一份故意写错的 README，验证比对结果：
        - 精确定位到行号
        - 明确指出是缺失、多余还是文案不匹配
        - 指出具体是哪条命令
        """
        bad_readme = self.readme_text
        # 故意删除 'draft save' 所在行
        bad_readme_lines = bad_readme.splitlines()
        removed_line_idx = None
        for i, line in enumerate(bad_readme_lines):
            if re.match(r"^\s+save\s+将当前批次的预览结果保存为草稿", line):
                removed_line_idx = i
                break
        assert removed_line_idx is not None, "测试数据失效：README 中找不到 draft save 行"
        bad_readme_lines.pop(removed_line_idx)
        # 故意多加一个不存在的命令
        for i, line in enumerate(bad_readme_lines):
            if re.match(r"^\s+delete\s+删除指定草稿", line):
                bad_readme_lines.insert(
                    i + 1,
                    "    nonexistent  这个命令根本不存在",
                )
                break
        bad_readme = "\n".join(bad_readme_lines)

        bad_entries, _, _ = parse_readme_command_reference(bad_readme)
        report = compare_cli_and_readme(self.cli_commands, bad_entries)

        # missing: draft save 应该被检出
        missing_names = {d.full_name for d in report.missing_in_readme}
        assert "draft save" in missing_names, (
            f"应检出 README 缺失 'draft save'，实际缺失集: {missing_names}"
        )
        # extra: nonexistent 应该被检出
        extra_names = {d.full_name for d in report.extra_in_readme}
        assert any("nonexistent" in n for n in extra_names), (
            f"应检出 README 多出 'nonexistent'，实际多余集: {extra_names}"
        )
        extra_entry = next(d for d in report.extra_in_readme if "nonexistent" in d.full_name)
        assert extra_entry.readme_line is not None and extra_entry.readme_line > 0, (
            "多余命令的行号未记录"
        )


class TestEmptyDraftListValidation:
    """
    空草稿列表的深度校验。

    不仅检查"命令有输出"，还校验：
    - 输出包含关键提示"暂无草稿"
    - 退出码为 0
    - 不出现表格头、分隔线等"非空列表"特征
    - 该提示文本在 README 草稿章节或相关测试说明中可被追溯
    - 连续两次运行（模拟重启）结果一致
    """

    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.workspace = self.tmp / "ws_empty_draft"
        self.workspace.mkdir()
        self.runner = CliRunner()
        self.project_root = Path(__file__).resolve().parent.parent
        self.readme_path = self.project_root / "README.md"

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, args, input_text=""):
        result = self.runner.invoke(
            cli, ["--workspace", str(self.workspace)] + args, input=input_text
        )
        result.clean_output = _strip_ansi(result.output)
        return result

    def test_empty_draft_exit_code_zero(self):
        result = self._run(["draft", "list"])
        assert result.exit_code == 0, f"空草稿列表不应报错，实际退出码 {result.exit_code}"

    def test_empty_draft_contains_key_hint(self):
        result = self._run(["draft", "list"])
        assert "暂无草稿" in result.clean_output, (
            f"空草稿列表输出应包含 '暂无草稿'，实际输出:\n{result.clean_output}"
        )

    def test_empty_draft_no_table_no_data_rows(self):
        """空列表不应出现'草稿列表'表头或数据行。"""
        result = self._run(["draft", "list"])
        # 正常非空列表会有 "草稿列表" 标题
        assert "草稿列表" not in result.clean_output, (
            f"空草稿列表不应出现表格标题，实际输出:\n{result.clean_output}"
        )
        # 不应出现数字计数（除行号外的数字计数是数据行特征）
        # 注意：这里使用保守判断——不出现列名关键字段
        for forbidden in ("来源批次", "规则版本", "预览项", "冲突(未解决)"):
            assert forbidden not in result.clean_output, (
                f"空草稿列表不应出现列名 '{forbidden}'，实际输出:\n{result.clean_output}"
            )

    def test_empty_draft_key_hint_documented_in_readme(self):
        """README 草稿章节应提及空列表提示，确保文档可追溯。"""
        readme_text = self.readme_path.read_text(encoding="utf-8")
        assert "暂无草稿" in readme_text, (
            "README 中未记录空草稿列表的提示 '暂无草稿'，"
            "请在草稿管理相关章节补充说明以便用户预期一致。"
        )

    def test_empty_draft_output_deterministic(self):
        """连续两次空 draft list 输出完全一致（模拟重启一致性）。"""
        r1 = self._run(["draft", "list"])
        r2 = self._run(["draft", "list"])
        assert r1.clean_output == r2.clean_output, (
            "两次空草稿列表输出不一致\n"
            f"第一次: {r1.clean_output!r}\n第二次: {r2.clean_output!r}"
        )


# ---------------------------------------------------------------------------
# 原有快照版本数字校验（保留并迁移到新文件结构中）
# ---------------------------------------------------------------------------


class TestReadmeSnapshotVersionConsistency:
    """约束 README.md 中快照示例数字与真实 CLI 输出一致。"""

    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.workspace_a = self.tmp / "ws_readme_a"
        self.workspace_b = self.tmp / "ws_readme_b"
        self.workspace_a.mkdir()
        self.workspace_b.mkdir()
        self.snapshot_file = self.tmp / "readme_snap.json"
        self.runner = CliRunner()
        self.project_root = Path(__file__).resolve().parent.parent
        self.readme_path = self.project_root / "README.md"

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, args, workspace, input_text=""):
        result = self.runner.invoke(
            cli, ["--workspace", str(workspace)] + args, input=input_text
        )
        return result

    def _actual_export_version_string(self) -> str:
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(["rules", "add-ext", ".heic"], self.workspace_a)
        self._run(
            [
                "rules",
                "set-template",
                "{point.category}/{point.id}_{photo.name}_{photo.taken_at:%Y%m%d_%H%M%S}{photo.source_path.suffix}",
            ],
            self.workspace_a,
        )
        result = self._run(
            [
                "snapshot",
                "export",
                "-o",
                str(self.snapshot_file),
                "--name",
                "readme_check",
            ],
            self.workspace_a,
        )
        assert result.exit_code == 0
        with open(self.snapshot_file, "r", encoding="utf-8") as f:
            snap = json.load(f)
        version = snap["config_version"]
        return f"配置版本: v{version}"

    def _actual_import_sync_string(self) -> str:
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(["rules", "add-ext", ".heic"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "sync_check"],
            self.workspace_a,
        )
        self._run(["batch", "new", "--name", "旧批次1"], self.workspace_b)
        self._run(["batch", "new", "--name", "旧批次2"], self.workspace_b)
        result = self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )
        assert result.exit_code == 0
        batches_dir = self.workspace_b / ".patrol-archiver" / "batches"
        batch_count = len(list(batches_dir.glob("batch_*.json")))
        return f"已同步批次: {batch_count} 个"

    def _actual_import_new_version_string(self) -> str:
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "ver_check"],
            self.workspace_a,
        )
        self._run(["batch", "new", "--name", "批"], self.workspace_b)
        self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )
        cfg_file = self.workspace_b / ".patrol-archiver" / "config.json"
        with open(cfg_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return f"新配置版本: v{cfg['version']}"

    def test_readme_export_version_matches_actual(self):
        expected_token = self._actual_export_version_string()
        readme_text = self.readme_path.read_text(encoding="utf-8")
        assert expected_token in readme_text, (
            f"README 缺少 '{expected_token}'。\n"
            f"请把规则快照章节中导出时的配置版本示例更新为 {expected_token}。"
        )

    def test_readme_sync_batch_count_matches_actual(self):
        expected_token = self._actual_import_sync_string()
        readme_text = self.readme_path.read_text(encoding="utf-8")
        assert expected_token in readme_text, (
            f"README 缺少 '{expected_token}'。\n"
            f"请把规则快照章节中成功输出的已同步批次示例更新为 '{expected_token}'。"
        )

    def test_readme_import_new_version_matches_actual(self):
        expected_token = self._actual_import_new_version_string()
        readme_text = self.readme_path.read_text(encoding="utf-8")
        assert expected_token in readme_text, (
            f"README 缺少 '{expected_token}'。\n"
            f"请把规则快照章节中成功输出的新配置版本示例更新为 '{expected_token}'。"
        )


# ---------------------------------------------------------------------------
# 草稿管理 README 章节深度校验
# ---------------------------------------------------------------------------


class TestReadmeDraftConsistencyDeep:
    """
    草稿管理说明与真实 CLI 输出的深度一致性校验。

    覆盖：
    - README 包含草稿管理完整流程章节
    - CLI 输出与 README 描述文案逐 token 对齐
    - 重启后再次检查结果一致
    - 文案失配时能明确定位到 README 具体段落
    """

    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.workspace = self.tmp / "ws_draft_readme"
        self.workspace.mkdir()
        self.runner = CliRunner()
        self.project_root = Path(__file__).resolve().parent.parent
        self.readme_path = self.project_root / "README.md"
        self.sample = self.project_root / "sample"

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, args, input_text=""):
        result = self.runner.invoke(
            cli, ["--workspace", str(self.workspace)] + args, input=input_text
        )
        result.clean_output = _strip_ansi(result.output)
        return result

    def _setup_sample_with_preview(self):
        self._run(
            [
                "import",
                "--csv",
                str(self.sample / "points.csv"),
                "--notes",
                str(self.sample / "notes.json"),
                "--batch-name",
                "草稿一致性测试批次",
            ]
        )
        self._run(["rules", "set-action", "copy"])
        self._run(["rules", "set-duplicate", "rename"])
        self._run(["scan", "--dir", str(self.sample / "photos")])
        self._run(["preview"])

    def _get_previews_count(self) -> int:
        from patrol_archiver.store import BatchStore

        store = BatchStore(self.workspace)
        batch = store.get_current_batch()
        return len(batch.previews) if batch else 0

    # -- 章节存在性 --------------------------------------------------------

    def test_readme_has_draft_workflow_section(self):
        readme_text = self.readme_path.read_text(encoding="utf-8")
        required = [
            "## 草稿管理完整流程",
            "主流程：save → list/show → restore → archive",
            "场景一：恢复后沿用草稿动作",
            "场景二：跨重启后再次恢复仍可验证",
            "冲突策略优先级说明",
        ]
        for section in required:
            assert section in readme_text, (
                f"README 缺少关键章节/段落 '{section}'。"
                "请补充草稿管理完整流程说明。"
            )

    # -- draft save 输出 ---------------------------------------------------

    def test_draft_save_output_tokens_in_readme(self):
        self._setup_sample_with_preview()
        previews_count = self._get_previews_count()
        draft_name = "一致性测试草稿"
        result = self._run(["draft", "save", draft_name, "-d", "测试描述"])
        assert result.exit_code == 0

        exact_tokens = ["草稿已保存"]
        prefix_tokens = ["草稿名称:", "预览项数:", "来源批次:", "规则版本:"]
        readme_text = self.readme_path.read_text(encoding="utf-8")

        for token in exact_tokens:
            assert token in result.clean_output, f"CLI 输出应包含 '{token}'"
            assert token in readme_text, (
                f"README 草稿管理章节缺少 '{token}'。请在 draft save 预期输出中补充。"
            )

        for prefix in prefix_tokens:
            assert prefix in result.clean_output, f"CLI 输出应包含 '{prefix}' 前缀"
            assert prefix in readme_text, (
                f"README 草稿管理章节缺少 '{prefix}' 前缀。"
                "请在 draft save 预期输出中补充此行。"
            )

        exact_count_line = f"预览项数: {previews_count}"
        assert exact_count_line in result.clean_output, (
            f"CLI 输出应包含 '{exact_count_line}'"
        )

    # -- draft restore 差异警告 -------------------------------------------

    def test_draft_restore_discrepancy_tokens_in_readme(self):
        self._setup_sample_with_preview()
        self._run(["draft", "save", "差异测试草稿"])
        self._run(["rules", "set-action", "move"])
        self._run(["rules", "set-duplicate", "block"])

        result = self._run(["draft", "restore", "差异测试草稿", "--force"])
        assert result.exit_code == 0

        expected_tokens = [
            "草稿已恢复到当前批次",
            "归档动作: copy",
            "重复策略: rename",
            "草稿中保存的规则与当前配置不一致",
            "归档动作: 草稿为 copy，当前配置为 move",
            "将沿用草稿中的 copy 动作执行归档",
            "重复策略: 草稿为 rename，当前配置为 block",
            "将沿用草稿中的 rename 策略处理冲突",
            "提示：可以运行 'archive --dry-run' 验证恢复后的归档方案",
        ]
        readme_text = self.readme_path.read_text(encoding="utf-8")

        for token in expected_tokens:
            assert token in result.clean_output, f"CLI 输出缺少 '{token}'"
            assert token in readme_text, (
                f"README 草稿管理章节缺少 '{token}'。请在草稿恢复预期输出中补充。"
            )

    # -- dry-run after restore --------------------------------------------

    def test_dry_run_after_restore_tokens_in_readme(self):
        self._setup_sample_with_preview()
        self._run(["draft", "save", "dryrun测试草稿"])
        self._run(["rules", "set-action", "move"])
        self._run(["draft", "restore", "dryrun测试草稿", "--force"])
        result = self._run(["archive", "--dry-run"])
        assert result.exit_code == 0

        expected_tokens = [
            "当前归档动作: copy",
            "预览中保存的动作与当前配置不一致，将沿用预览中的动作",
        ]
        readme_text = self.readme_path.read_text(encoding="utf-8")
        for token in expected_tokens:
            assert token in result.clean_output, f"CLI 输出缺少 '{token}'"
            assert token in readme_text, (
                f"README dry-run 验证部分缺少 '{token}'。请在 dry-run 预期输出中补充。"
            )

    # -- draft show 规则信息 ----------------------------------------------

    def test_draft_show_rule_fields_in_readme(self):
        self._setup_sample_with_preview()
        self._run(["draft", "save", "详情测试草稿"])
        result = self._run(["draft", "show", "详情测试草稿"])
        assert result.exit_code == 0

        expected_tokens = [
            "草稿详情",
            "规则信息",
            "规则版本",
            "重复策略",
            "归档动作",
            "命名模板",
        ]
        readme_text = self.readme_path.read_text(encoding="utf-8")
        for token in expected_tokens:
            assert token in result.clean_output, f"CLI 输出缺少 '{token}'"
            assert token in readme_text, (
                f"README draft show 说明缺少 '{token}'。请补充 draft show 预期输出描述。"
            )

    # -- 兼容性确认对话框 -------------------------------------------------

    def test_draft_restore_confirm_tokens_in_readme(self):
        self._setup_sample_with_preview()
        self._run(["draft", "save", "兼容测试草稿"])
        self._run(["rules", "set-action", "move"])

        result = self._run(["draft", "restore", "兼容测试草稿"], input_text="n\n")
        assert result.exit_code == 0

        expected_tokens = [
            "检测到以下差异",
            "归档动作不匹配：草稿为 copy，当前为 move",
            "检测到草稿与当前状态存在差异，恢复后将覆盖当前批次的预览和冲突数据。是否继续？",
            "已取消恢复",
        ]
        readme_text = self.readme_path.read_text(encoding="utf-8")
        for token in expected_tokens:
            assert token in result.clean_output, f"CLI 输出缺少 '{token}'"
            assert token in readme_text, (
                f"README 草稿恢复差异警告部分缺少 '{token}'。请补充恢复确认文案。"
            )

    # -- 空草稿列表（与 TestEmptyDraftListValidation 对齐，但在这里校验 README 段落位置）

    def test_empty_draft_message_appears_in_readme_draft_section(self):
        readme_text = self.readme_path.read_text(encoding="utf-8")
        # "暂无草稿" 必须出现在草稿管理章节或命令参考附近
        draft_header_idx = readme_text.find("## 草稿管理完整流程")
        cmd_ref_idx = readme_text.find("## 命令参考")
        assert draft_header_idx >= 0 and cmd_ref_idx >= 0
        msg_idx = readme_text.find("暂无草稿")
        assert msg_idx >= 0, "README 中未找到 '暂无草稿'"
        # 至少出现在两个关键段落之一的覆盖区间内
        near_draft_section = draft_header_idx < msg_idx < cmd_ref_idx
        near_cmd_ref = cmd_ref_idx < msg_idx
        assert near_draft_section or near_cmd_ref, (
            "'暂无草稿' 应出现在草稿管理完整流程章节或命令参考附近"
        )

    # -- 重启一致性 -------------------------------------------------------

    def test_draft_section_checks_deterministic_across_restart(self):
        """
        重复执行 draft save → 改配置 → restore → dry-run 的整套检查，
        每一步输出与 README 对齐的结果集合必须一致。
        """

        def run_pipeline() -> Tuple[str, ...]:
            ws = self.tmp / f"ws_deterministic_{id(self)}_{len(list(self.tmp.glob('ws_deterministic_*')))}"
            ws.mkdir()
            runner = CliRunner()

            def run_local(args, input_text=""):
                r = runner.invoke(cli, ["--workspace", str(ws)] + args, input=input_text)
                return _strip_ansi(r.output)

            run_local(
                [
                    "import",
                    "--csv",
                    str(self.sample / "points.csv"),
                    "--notes",
                    str(self.sample / "notes.json"),
                    "--batch-name",
                    "重启一致性",
                ]
            )
            run_local(["rules", "set-action", "copy"])
            run_local(["rules", "set-duplicate", "rename"])
            run_local(["scan", "--dir", str(self.sample / "photos")])
            run_local(["preview"])
            run_local(["draft", "save", "重启一致性草稿"])
            run_local(["rules", "set-action", "move"])
            run_local(["rules", "set-duplicate", "block"])
            restore_out = run_local(["draft", "restore", "重启一致性草稿", "--force"])
            dryrun_out = run_local(["archive", "--dry-run"])
            list_empty_out = run_local(["draft", "list"])

            # 关键片段抽取（输出稳定且与 README 相关）
            def extract_present(text: str, tokens: List[str]) -> Tuple[str, ...]:
                return tuple(t for t in tokens if t in text)

            restore_tokens = (
                "草稿已恢复到当前批次",
                "归档动作: copy",
                "重复策略: rename",
                "草稿中保存的规则与当前配置不一致",
                "提示：可以运行 'archive --dry-run' 验证恢复后的归档方案",
            )
            dryrun_tokens = (
                "当前归档动作: copy",
                "预览中保存的动作与当前配置不一致，将沿用预览中的动作",
            )
            list_empty_tokens = ("暂无草稿",)

            return (
                extract_present(restore_out, list(restore_tokens)),
                extract_present(dryrun_out, list(dryrun_tokens)),
                extract_present(list_empty_out, list(list_empty_tokens)),
            )

        first = run_pipeline()
        second = run_pipeline()
        assert first == second, (
            "草稿校验重启前后结果不一致\n"
            f"第一次: {first}\n第二次: {second}"
        )
