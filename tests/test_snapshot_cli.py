from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import List

from click.testing import CliRunner

from patrol_archiver.cli import cli


class TestSnapshotCliEndToEnd:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.workspace_a = self.tmp / "workspace_a"
        self.workspace_b = self.tmp / "workspace_b"
        self.workspace_a.mkdir()
        self.workspace_b.mkdir()
        self.snapshot_file = self.tmp / "rules_snapshot.json"
        self.runner = CliRunner()

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, args: List[str], workspace: Path, input_text: str = ""):
        cmd_args = ["--workspace", str(workspace)] + args
        result = self.runner.invoke(cli, cmd_args, input=input_text)
        return result

    def test_snapshot_export_cli(self):
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(["rules", "add-ext", ".heic"], self.workspace_a)

        result = self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "测试快照", "--description", "端到端测试", "--author", "tester"],
            self.workspace_a,
        )

        assert result.exit_code == 0
        assert "规则快照已导出" in result.output
        assert "测试快照" in result.output
        assert "tester" in result.output
        assert self.snapshot_file.exists()

        with open(self.snapshot_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["name"] == "测试快照"
        assert data["description"] == "端到端测试"
        assert data["created_by"] == "tester"
        assert data["duplicate_strategy"] == "rename"
        assert ".heic" in data["allowed_extensions"]

    def test_snapshot_show_file_cli(self):
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "展示测试"],
            self.workspace_a,
        )

        result = self._run(["snapshot", "show", "-f", str(self.snapshot_file)], self.workspace_a)

        assert result.exit_code == 0
        assert "展示测试" in result.output
        assert "配置版本" in result.output
        assert "命名模板" in result.output
        assert "允许的扩展名" in result.output
        assert "重复策略" in result.output

    def test_snapshot_import_with_force_cli(self):
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(["rules", "add-ext", ".heic"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "强制导入测试"],
            self.workspace_a,
        )

        self._run(["batch", "new", "--name", "批次1"], self.workspace_b)
        self._run(["batch", "new", "--name", "批次2"], self.workspace_b)

        result = self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force", "--author", "import_user"],
            self.workspace_b,
        )

        assert result.exit_code == 0
        assert "快照导入成功" in result.output
        assert "已同步批次: 2 个" in result.output
        assert "操作已记录到日志" in result.output

    def test_snapshot_import_conflict_detection_cli(self):
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "冲突检测测试"],
            self.workspace_a,
        )

        self._run(["batch", "new", "--name", "旧批次"], self.workspace_b)

        result = self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )

        assert result.exit_code == 0
        assert "检测到" in result.output
        assert "个冲突" in result.output
        assert "策略差异" in result.output
        assert "批次存在" in result.output

    def test_snapshot_import_cancel_cli(self):
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "取消测试"],
            self.workspace_a,
        )

        self._run(["batch", "new", "--name", "取消批次"], self.workspace_b)
        config_before = self._get_config_version(self.workspace_b)
        batch_version_before = self._get_batch_config_version(self.workspace_b)

        result = self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file)],
            self.workspace_b,
            input_text="n\n",
        )

        assert result.exit_code == 0
        assert "已取消导入" in result.output

        config_after = self._get_config_version(self.workspace_b)
        batch_version_after = self._get_batch_config_version(self.workspace_b)
        assert config_before == config_after
        assert batch_version_before == batch_version_after

    def test_snapshot_import_confirm_cli(self):
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "确认测试"],
            self.workspace_a,
        )

        self._run(["batch", "new", "--name", "确认批次"], self.workspace_b)
        config_before = self._get_config_version(self.workspace_b)

        result = self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--author", "confirmer"],
            self.workspace_b,
            input_text="y\n",
        )

        assert result.exit_code == 0
        assert "快照导入成功" in result.output

        config_after = self._get_config_version(self.workspace_b)
        assert config_after > config_before

    def test_snapshot_log_cli(self):
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "日志测试"],
            self.workspace_a,
        )

        self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )

        result = self._run(["snapshot", "log"], self.workspace_b)

        assert result.exit_code == 0
        assert "快照导入日志" in result.output
        assert "日志测试" in result.output
        assert "success" in result.output
        assert "v1 → v" in result.output

    def test_snapshot_show_current_cli(self):
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "当前快照测试"],
            self.workspace_a,
        )

        self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force", "--author", "user_x"],
            self.workspace_b,
        )

        result = self._run(["snapshot", "show"], self.workspace_b)

        assert result.exit_code == 0
        assert "最近导入的快照" in result.output
        assert "当前快照测试" in result.output
        assert "user_x" in result.output

    def test_rules_show_includes_snapshot_info_cli(self):
        self._run(["rules", "set-duplicate", "skip"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "rules show测试"],
            self.workspace_a,
        )

        self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )

        result = self._run(["rules", "show"], self.workspace_b)

        assert result.exit_code == 0
        assert "最近快照导入" in result.output
        assert "rules show测试" in result.output

    def test_info_includes_snapshot_info_cli(self):
        self._run(["rules", "set-duplicate", "overwrite"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "info测试"],
            self.workspace_a,
        )

        self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )

        result = self._run(["info"], self.workspace_b)

        assert result.exit_code == 0
        assert "最近快照导入" in result.output
        assert "info测试" in result.output

    def test_import_then_export_markdown_uses_new_version_cli(self):
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(["rules", "add-ext", ".raw"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "导出测试"],
            self.workspace_a,
        )

        self._run(["batch", "new", "--name", "导出批次"], self.workspace_b)
        self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )

        md_path = self.workspace_b / "test_report.md"
        result = self._run(["export", "markdown", "-o", str(md_path)], self.workspace_b)

        assert result.exit_code == 0
        assert "Markdown 报告已导出" in result.output
        assert md_path.exists()

        content = md_path.read_text(encoding="utf-8")
        assert "配置版本" in content
        assert "v2" in content

    def test_import_then_export_csv_uses_new_version_cli(self):
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "CSV测试"],
            self.workspace_a,
        )

        self._run(["batch", "new", "--name", "CSV批次"], self.workspace_b)
        self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )

        csv_path = self.workspace_b / "test_report.csv"
        result = self._run(["export", "csv", "-o", str(csv_path)], self.workspace_b)

        assert result.exit_code == 0
        assert csv_path.exists()

        content = csv_path.read_text(encoding="utf-8-sig")
        assert "config_version" in content

    def test_switch_old_batch_after_import_export_still_uses_new_version_cli(self):
        self._run(["rules", "set-duplicate", "skip"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "切换批次测试"],
            self.workspace_a,
        )

        self._run(["batch", "new", "--name", "旧批次1"], self.workspace_b)
        old_batch_result = self._run(["batch", "new", "--name", "旧批次2"], self.workspace_b)

        self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )

        self._run(["batch", "switch", "旧批次1"], self.workspace_b)

        md_path = self.workspace_b / "old_batch_report.md"
        result = self._run(["export", "markdown", "-o", str(md_path)], self.workspace_b)

        assert result.exit_code == 0
        content = md_path.read_text(encoding="utf-8")
        assert "v2" in content

    def test_multiple_imports_logged_separately_cli(self):
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "第一次"],
            self.workspace_a,
        )

        self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )

        self._run(["rules", "set-duplicate", "skip"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "第二次"],
            self.workspace_a,
        )

        self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )

        result = self._run(["snapshot", "log", "-n", "5"], self.workspace_b)

        assert result.exit_code == 0
        assert "第一次" in result.output
        assert "第二次" in result.output

    def test_empty_workspace_no_snapshot_show_shows_message_cli(self):
        result = self._run(["snapshot", "show"], self.workspace_b)
        assert result.exit_code == 0
        assert "尚未导入过规则快照" in result.output

    def test_snapshot_log_empty_cli(self):
        result = self._run(["snapshot", "log"], self.workspace_b)
        assert result.exit_code == 0
        assert "暂无快照导入记录" in result.output

    def test_batch_show_shows_updated_version_after_import_cli(self):
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file)],
            self.workspace_a,
        )

        self._run(["batch", "new", "--name", "test_batch"], self.workspace_b)
        self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )

        result = self._run(["batch", "show"], self.workspace_b)
        assert result.exit_code == 0
        assert "配置版本: v2" in result.output

    def _get_config_version(self, workspace: Path) -> int:
        config_file = workspace / ".patrol-archiver" / "config.json"
        if not config_file.exists():
            return 0
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("version", 0)

    def _get_batch_config_version(self, workspace: Path) -> int:
        batches_dir = workspace / ".patrol-archiver" / "batches"
        if not batches_dir.exists():
            return 0
        batch_files = sorted(batches_dir.glob("batch_*.json"))
        if not batch_files:
            return 0
        with open(batch_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("config_version", 0)
