from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from click.testing import CliRunner

from patrol_archiver.cli import cli


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
