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

    def test_export_version_after_three_rule_changes_cli(self):
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(["rules", "add-ext", ".heic"], self.workspace_a)
        self._run(
            ["rules", "set-template", "{point.category}/{point.id}_{point.name}_{photo.taken_at:%Y%m%d_%H%M%S}{photo.source_path.suffix}"],
            self.workspace_a,
        )

        result = self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "版本号测试"],
            self.workspace_a,
        )

        assert result.exit_code == 0
        assert "配置版本: v4" in result.output

        with open(self.snapshot_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["config_version"] == 4

    def test_import_sync_batch_count_matches_actual_batches_cli(self):
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        self._run(["rules", "add-ext", ".heic"], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "批次数测试"],
            self.workspace_a,
        )

        self._run(["batch", "new", "--name", "旧批次1"], self.workspace_b)
        self._run(["batch", "new", "--name", "旧批次2"], self.workspace_b)

        result = self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )

        assert result.exit_code == 0
        assert "已同步批次: 2 个" in result.output

    def _setup_sample_data(self, workspace: Path):
        project = Path(__file__).resolve().parent.parent
        sample = project / "sample"
        self._run(
            ["import", "--csv", str(sample / "points.csv"),
             "--notes", str(sample / "notes.json"), "--batch-name", "测试"],
            workspace,
        )
        self._run(["scan", "--dir", str(sample / "photos")], workspace)

    def _batch_metrics(self, workspace: Path):
        batches_dir = workspace / ".patrol-archiver" / "batches"
        batch_files = sorted(batches_dir.glob("batch_*.json"))
        if not batch_files:
            return 0, 0
        with open(batch_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)
        return len(data.get("previews", [])), len(data.get("conflicts", []))

    def _batch_conflicts(self, workspace: Path) -> list:
        batches_dir = workspace / ".patrol-archiver" / "batches"
        batch_files = sorted(batches_dir.glob("batch_*.json"))
        if not batch_files:
            return []
        with open(batch_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("conflicts", [])

    def test_photo_name_alias_backward_compat_preview_ok_cli(self):
        bad_template = "{point.category}/{point.id}_{photo.name}_{photo.taken_at:%Y%m%d_%H%M%S}{photo.source_path.suffix}"
        self._run(["rules", "set-template", bad_template], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "误用模板快照"],
            self.workspace_a,
        )
        self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )
        self._setup_sample_data(self.workspace_b)
        preview_result = self._run(["preview"], self.workspace_b)
        assert preview_result.exit_code == 0

        previews, _ = self._batch_metrics(self.workspace_b)
        render_fails = [
            c for c in self._batch_conflicts(self.workspace_b)
            if "模板渲染失败" in (c.get("reason", "") or "")
        ]
        assert previews > 0, (
            f"应生成预览项，实际 {previews} 个，"
            f"模板渲染失败冲突: {len(render_fails)}"
        )

    def test_set_template_photo_name_emits_alias_warning_cli(self):
        result = self._run(
            ["rules", "set-template", "{photo.name}_{photo.taken_at:%Y%m%d}{photo.source_path.suffix}"],
            self.workspace_a,
        )
        assert result.exit_code == 0
        assert "别名" in result.output
        assert "photo.file_name" in result.output

    def test_snapshot_import_photo_name_emits_alias_warning_cli(self):
        bad_template = "{point.id}_{photo.name}_{photo.source_path.suffix}"
        self._run(["rules", "set-template", bad_template], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "含别名快照"],
            self.workspace_a,
        )
        result = self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )
        assert result.exit_code == 0
        assert "别名" in result.output

    def test_set_template_unknown_variable_warning_cli(self):
        result = self._run(
            ["rules", "set-template", "{photo.nonexist}_{point.id}"],
            self.workspace_a,
        )
        assert result.exit_code != 0
        assert "命名模板更新失败" in result.output
        assert "未知模板变量" in result.output

    def test_illegal_template_not_persisted_cli(self):
        """非法模板必须在持久化前拒绝，配置版本保持不变。"""
        r1 = self._run(["rules", "show"], self.workspace_a)
        assert r1.exit_code == 0
        version_before = self._read_config_version(self.workspace_a)

        r2 = self._run(
            ["rules", "set-template", "{point.id}_{photo.bad_attr}_{photo.another_bad}"],
            self.workspace_a,
        )
        assert r2.exit_code != 0
        assert "photo.bad_attr" in r2.output and "photo.another_bad" in r2.output

        version_after = self._read_config_version(self.workspace_a)
        assert version_after == version_before

    def test_snapshot_import_illegal_template_rejected_cli(self):
        """含非法变量的快照导入必须在落库前被拒绝，配置保持不变。"""
        self._run(["rules", "set-duplicate", "rename"], self.workspace_a)
        tpl_before_a = self._read_naming_template(self.workspace_a)
        snap_content_illegal = {
            "snapshot_id": "snap_illegal",
            "name": "非法模板快照",
            "description": "",
            "config_version": 1,
            "created_by": "tester",
            "created_at": "2026-06-19T00:00:00",
            "naming_template": "{point.id}_{photo.nonexist_attr}_{photo.source_path.suffix}",
            "allowed_extensions": [".jpg"],
            "duplicate_strategy": "skip",
            "archive_action": "copy",
            "archive_dir": "archive",
            "photo_dir": "photos",
            "notes_json": "notes.json",
            "points_csv": "points.csv",
            "default_author": "tester",
        }
        with open(self.snapshot_file, "w", encoding="utf-8") as f:
            json.dump(snap_content_illegal, f, ensure_ascii=False)

        self._run(["rules", "show"], self.workspace_b)
        cfg_before = self._read_config_version(self.workspace_b)
        tpl_before_b = self._read_naming_template(self.workspace_b)

        result = self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )
        assert result.exit_code != 0
        assert "快照导入失败" in result.output
        assert "命名模板不合法" in result.output
        assert "photo.nonexist_attr" in result.output

        cfg_after = self._read_config_version(self.workspace_b)
        tpl_after_b = self._read_naming_template(self.workspace_b)
        assert cfg_after == cfg_before, (
            f"拒绝导入后配置版本不应该递增: {cfg_before} -> {cfg_after}"
        )
        assert tpl_after_b == tpl_before_b, (
            f"拒绝导入后命名模板不应该被替换"
        )
        assert self._read_naming_template(self.workspace_a) == tpl_before_a

    def test_legal_template_with_alias_still_accepted_cli(self):
        """含别名 {photo.name} 的模板合法（只是警告），不被拒绝。"""
        result = self._run(
            ["rules", "set-template", "{point.id}_{photo.name}_{photo.source_path.suffix}"],
            self.workspace_a,
        )
        assert result.exit_code == 0
        assert "命名模板已更新" in result.output
        assert "别名" in result.output

    def test_legal_snapshot_import_with_alias_still_works_cli(self):
        """含别名 {photo.name} 的快照导入正常成功（只是警告），preview 可用。"""
        alias_tpl = "{point.id}_{photo.name}_{photo.source_path.suffix}"
        self._run(["rules", "set-template", alias_tpl], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "别名快照"],
            self.workspace_a,
        )

        result = self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )
        assert result.exit_code == 0
        assert "快照导入成功" in result.output
        assert "别名" in result.output

        self._setup_sample_data(self.workspace_b)
        preview = self._run(["preview"], self.workspace_b)
        assert preview.exit_code == 0
        previews, _ = self._batch_metrics(self.workspace_b)
        assert previews >= 9

    def _read_config_version(self, workspace: Path) -> int:
        cf = workspace / ".patrol-archiver" / "config.json"
        if not cf.exists():
            return 0
        with open(cf, "r", encoding="utf-8") as f:
            return json.load(f).get("version", 0)

    def _read_naming_template(self, workspace: Path) -> str:
        cf = workspace / ".patrol-archiver" / "config.json"
        if not cf.exists():
            return ""
        with open(cf, "r", encoding="utf-8") as f:
            return json.load(f).get("naming_template", "")

    def test_readme_snapshot_example_template_preview_ok_cli(self):
        readme_tpl = "{point.category}/{point.id}_{point.name}_{photo.taken_at:%Y%m%d_%H%M%S}{photo.source_path.suffix}"
        self._run(["rules", "set-template", readme_tpl], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "README示例"],
            self.workspace_a,
        )
        self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )
        self._setup_sample_data(self.workspace_b)
        preview_result = self._run(["preview"], self.workspace_b)
        assert preview_result.exit_code == 0

        previews, _ = self._batch_metrics(self.workspace_b)
        template_errors = [
            c for c in self._batch_conflicts(self.workspace_b)
            if "模板渲染失败" in (c.get("reason", "") or "")
        ]
        assert previews >= 9
        assert len(template_errors) == 0, f"存在模板渲染失败: {template_errors[:3]}"

    def test_nested_illegal_photo_attr_blocked_at_set_template_cli(self):
        """非法嵌套 photo 属性在 set-template 时被拦截，不落库。"""
        version_before = self._read_config_version(self.workspace_a)
        result = self._run(
            ["rules", "set-template", "{point.id}_{photo.source_path.nonexist}"],
            self.workspace_a,
        )
        assert result.exit_code != 0
        assert "命名模板更新失败" in result.output
        assert "photo.source_path.nonexist" in result.output
        assert self._read_config_version(self.workspace_a) == version_before

    def test_nested_illegal_point_attr_blocked_at_set_template_cli(self):
        """非法嵌套 point 属性在 set-template 时被拦截。"""
        result = self._run(
            ["rules", "set-template", "{point.id.bogus}_{photo.source_path.suffix}"],
            self.workspace_a,
        )
        assert result.exit_code != 0
        assert "point.id.bogus" in result.output

    def test_nested_illegal_photo_attr_in_snapshot_import_blocked_cli(self):
        """含非法嵌套 photo 属性的快照导入被拦截，配置不变。"""
        self._run(["rules", "show"], self.workspace_b)
        cfg_before = self._read_config_version(self.workspace_b)
        tpl_before = self._read_naming_template(self.workspace_b)
        snap_data = {
            "snapshot_id": "snap_nested_bad",
            "name": "非法嵌套快照",
            "description": "",
            "config_version": 1,
            "created_by": "tester",
            "created_at": "2026-06-19T00:00:00",
            "naming_template": "{point.id}_{photo.source_path.NOPE}",
            "allowed_extensions": [".jpg"],
            "duplicate_strategy": "skip",
            "archive_action": "copy",
            "archive_dir": "archive",
            "photo_dir": "photos",
            "notes_json": "notes.json",
            "points_csv": "points.csv",
            "default_author": "tester",
        }
        with open(self.snapshot_file, "w", encoding="utf-8") as f:
            json.dump(snap_data, f, ensure_ascii=False)
        result = self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )
        assert result.exit_code != 0
        assert "快照导入失败" in result.output
        assert "photo.source_path.NOPE" in result.output
        assert self._read_config_version(self.workspace_b) == cfg_before
        assert self._read_naming_template(self.workspace_b) == tpl_before

    def test_legal_nested_attrs_set_and_preview_ok_cli(self):
        """合法嵌套属性 source_path.suffix/stem/name 正常设置并可预览。"""
        for tpl in [
            "{point.id}_{photo.source_path.suffix}",
            "{point.id}_{photo.source_path.stem}",
            "{point.id}_{photo.source_path.name}",
        ]:
            result = self._run(["rules", "set-template", tpl], self.workspace_a)
            assert result.exit_code == 0, f"合法模板 {tpl} 应被接受"

    def test_source_path_suffix_full_chain_ok_cli(self):
        """{photo.source_path.suffix} 走完 export→import→preview 全链路。"""
        tpl = "{point.category}/{point.id}_{photo.source_path.suffix}"
        self._run(["rules", "set-template", tpl], self.workspace_a)
        self._run(
            ["snapshot", "export", "-o", str(self.snapshot_file), "--name", "嵌套链路"],
            self.workspace_a,
        )
        result = self._run(
            ["snapshot", "import", "-f", str(self.snapshot_file), "--force"],
            self.workspace_b,
        )
        assert result.exit_code == 0
        self._setup_sample_data(self.workspace_b)
        preview = self._run(["preview"], self.workspace_b)
        assert preview.exit_code == 0
        previews, _ = self._batch_metrics(self.workspace_b)
        assert previews >= 9
