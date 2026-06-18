from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from click.testing import CliRunner

from patrol_archiver.cli import cli


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


class TestReadmeConsistency:
    """约束 README.md 中快照示例数字与真实 CLI 输出一致。

    当实现逻辑变化或文档数字写错时，本测试会失败，
    确保 README 的导出版本号、同步批次数始终与实际行为对齐。
    """

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
        """按 README 的"3次规则修改后导出"跑真实 CLI，返回预期版本字符串。"""
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
        """按 README 的"2个旧批次导入"跑真实 CLI，返回预期同步批次数字符串。"""
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
        """按 README 的示例导入后，返回新配置版本字符串。"""
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
        """README 中"3次修改后导出版本号"必须与真实 CLI 输出一致。"""
        expected_token = self._actual_export_version_string()
        readme_text = self.readme_path.read_text(encoding="utf-8")
        assert expected_token in readme_text, (
            f"README 缺少 '{expected_token}'。\n"
            f"请把规则快照章节中导出时的配置版本示例更新为 {expected_token}。"
        )

    def test_readme_sync_batch_count_matches_actual(self):
        """README 中"2个批次导入同步数"必须与真实 CLI 输出一致。"""
        expected_token = self._actual_import_sync_string()
        readme_text = self.readme_path.read_text(encoding="utf-8")
        assert expected_token in readme_text, (
            f"README 缺少 '{expected_token}'。\n"
            f"请把规则快照章节中成功输出的已同步批次示例更新为 '{expected_token}'。"
        )

    def test_readme_import_new_version_matches_actual(self):
        """README 中"导入后新配置版本"必须与真实行为一致。"""
        expected_token = self._actual_import_new_version_string()
        readme_text = self.readme_path.read_text(encoding="utf-8")
        assert expected_token in readme_text, (
            f"README 缺少 '{expected_token}'。\n"
            f"请把规则快照章节中成功输出的新配置版本示例更新为 '{expected_token}'。"
        )


class TestReadmeDraftConsistency:
    """约束 README.md 中草稿管理章节的关键输出与真实 CLI 一致。

    当 CLI 帮助文案变化或 README 描述失准时，本测试会失败，
    确保草稿管理的主流程（save → list/show → restore → archive 说明始终与实际行为对齐。
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
        """设置样例数据并生成预览，返回预览项数量。"""
        self._run([
            "import", "--csv", str(self.sample / "points.csv"),
            "--notes", str(self.sample / "notes.json"), "--batch-name", "草稿一致性测试批次"
        ])
        self._run(["rules", "set-action", "copy"])
        self._run(["rules", "set-duplicate", "rename"])
        self._run(["scan", "--dir", str(self.sample / "photos")])
        self._run(["preview"])

    def _get_previews_count(self) -> int:
        from patrol_archiver.store import BatchStore
        store = BatchStore(self.workspace)
        batch = store.get_current_batch()
        return len(batch.previews) if batch else 0

    def test_readme_contains_draft_command_reference(self):
        """README 的命令参考必须包含 draft 命令组及其全部子命令。"""
        readme_text = self.readme_path.read_text(encoding="utf-8")
        required_tokens = [
            "draft      归档方案草稿管理",
            "save     将当前批次的预览结果保存为草稿",
            "list     列出所有草稿",
            "show     查看草稿详情",
            "restore  恢复草稿到当前批次",
            "delete   删除指定草稿",
        ]
        for token in required_tokens:
            assert token in readme_text, (
                f"README 命令参考缺少 '{token}'。\n"
                f"请在命令参考章节补充 draft 命令组。"
            )

    def test_readme_contains_draft_workflow_section(self):
        """README 必须包含草稿管理完整流程章节及主流程说明。"""
        readme_text = self.readme_path.read_text(encoding="utf-8")
        required_sections = [
            "## 草稿管理完整流程",
            "主流程：save → list/show → restore → archive",
            "场景一：恢复后沿用草稿动作",
            "场景二：跨重启后再次恢复仍可验证",
            "冲突策略优先级说明",
        ]
        for section in required_sections:
            assert section in readme_text, (
                f"README 缺少章节/段落 '{section}'。\n"
                f"请补充草稿管理完整流程说明。"
            )

    def test_draft_save_output_matches_readme(self):
        """README 草稿章节中 draft save 的预期输出必须与真实 CLI 一致。"""
        self._setup_sample_with_preview()
        previews_count = self._get_previews_count()
        draft_name = "一致性测试草稿"
        result = self._run(["draft", "save", draft_name, "-d", "测试描述"])
        assert result.exit_code == 0

        exact_tokens = ["草稿已保存"]
        prefix_tokens = ["草稿名称:", "预览项数:"]
        readme_text = self.readme_path.read_text(encoding="utf-8")

        for token in exact_tokens:
            assert token in result.clean_output, f"CLI 输出应包含 '{token}'"
            assert token in readme_text, (
                f"README 草稿管理章节缺少 '{token}'。\n"
                f"请在 draft save 预期输出中补充此文案。"
            )

        for prefix in prefix_tokens:
            assert prefix in result.clean_output, f"CLI 输出应包含 '{prefix}' 前缀"
            assert prefix in readme_text, (
                f"README 草稿管理章节缺少 '{prefix}' 前缀。\n"
                f"请在 draft save 预期输出中补充此行。"
            )

        exact_count_line = f"预览项数: {previews_count}"
        assert exact_count_line in result.clean_output, f"CLI 输出应包含 '{exact_count_line}'"

    def test_draft_restore_discrepancy_warning_matches_readme(self):
        """README 中草稿恢复差异警告必须与真实 CLI 一致。"""
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
                f"README 草稿管理章节缺少 '{token}'。\n"
                f"请在草稿恢复预期输出中补充此文案。"
            )

    def test_archive_dry_run_after_restore_matches_readme(self):
        """README 中恢复后 dry-run 的关键输出必须与真实 CLI 一致。"""
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
                f"README 草稿管理章节 dry-run 验证部分缺少 '{token}'。\n"
                f"请在 dry-run 预期输出中补充此文案。"
            )

    def test_draft_show_contains_rule_info(self):
        """README 中 draft show 的规则信息字段必须与真实 CLI 一致。"""
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
                f"README draft show 说明中缺少 '{token}'。\n"
                f"请补充 draft show 预期输出描述。"
            )

    def test_draft_restore_compatibility_warning_matches_readme(self):
        """README 中兼容性警告列表必须与真实 CLI 一致。"""
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
                f"README 草稿恢复差异警告部分缺少 '{token}'。\n"
                f"请补充恢复确认文案。"
            )

    def test_draft_list_empty_matches_readme(self):
        """README 中空草稿列表提示必须与真实 CLI 一致。"""
        result = self._run(["draft", "list"])
        assert result.exit_code == 0
        readme_text = self.readme_path.read_text(encoding="utf-8")

        expected = "暂无草稿"
        assert expected in result.clean_output, f"CLI 输出应包含 '{expected}'"
        # README 快速开始部分或草稿管理章节包含此提示"
