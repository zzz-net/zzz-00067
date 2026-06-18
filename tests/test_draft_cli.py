from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import List

from click.testing import CliRunner

from patrol_archiver.cli import cli
from patrol_archiver.store import BatchStore


class TestDraftCliEndToEnd:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.workspace = self.tmp / "workspace"
        self.workspace.mkdir()
        self.runner = CliRunner()
        self.project = Path(__file__).resolve().parent.parent
        self.sample = self.project / "sample"

    def teardown_method(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, args: List[str], input_text: str = ""):
        cmd_args = ["--workspace", str(self.workspace)] + args
        result = self.runner.invoke(cli, cmd_args, input=input_text)
        return result

    def _setup_sample_data(self):
        self._run([
            "import", "--csv", str(self.sample / "points.csv"),
            "--notes", str(self.sample / "notes.json"), "--batch-name", "测试批次"
        ])
        self._run(["scan", "--dir", str(self.sample / "photos")])
        self._run(["preview"])

    def _get_draft_count(self) -> int:
        drafts_dir = self.workspace / ".patrol-archiver" / "drafts"
        if not drafts_dir.exists():
            return 0
        return len(list(drafts_dir.glob("draft_*.json")))

    def _get_batch_previews_count(self) -> int:
        store = BatchStore(self.workspace)
        batch = store.get_current_batch()
        return len(batch.previews) if batch else 0

    def _get_batch_conflicts_count(self) -> int:
        store = BatchStore(self.workspace)
        batch = store.get_current_batch()
        return len(batch.conflicts) if batch else 0

    def test_draft_save_requires_preview(self):
        """没有预览时保存草稿应该失败"""
        self._run([
            "import", "--csv", str(self.sample / "points.csv"),
            "--batch-name", "测试批次"
        ])
        result = self._run(["draft", "save", "草稿1"])
        assert result.exit_code != 0
        assert "没有预览数据" in result.output

    def test_draft_save_success(self):
        """正常保存草稿"""
        self._setup_sample_data()

        previews_before = self._get_batch_previews_count()
        conflicts_before = self._get_batch_conflicts_count()

        result = self._run(["draft", "save", "测试草稿", "-d", "测试描述"])
        assert result.exit_code == 0
        assert "草稿已保存" in result.output
        assert "测试草稿" in result.output
        assert "测试批次" in result.output
        assert str(previews_before) in result.output

        assert self._get_draft_count() == 1

    def test_draft_list_empty(self):
        """没有草稿时列表显示为空"""
        result = self._run(["draft", "list"])
        assert result.exit_code == 0
        assert "暂无草稿" in result.output

    def test_draft_list_with_drafts(self):
        """列出草稿"""
        self._setup_sample_data()
        self._run(["draft", "save", "草稿1", "-d", "第一个草稿"])
        self._run(["draft", "save", "草稿2", "-d", "第二个草稿"])

        result = self._run(["draft", "list"])
        assert result.exit_code == 0
        assert "草稿列表" in result.output
        assert "草稿1" in result.output
        assert "草稿2" in result.output
        assert "草稿列表" in result.output

    def test_draft_show_detail(self):
        """查看草稿详情"""
        self._setup_sample_data()
        self._run(["draft", "save", "详情测试草稿", "-d", "测试详情"])

        result = self._run(["draft", "show", "详情测试草稿"])
        assert result.exit_code == 0
        assert "草稿详情" in result.output
        assert "详情测试草稿" in result.output
        assert "测试详情" in result.output
        assert "规则版本" in result.output
        assert "重复策略" in result.output
        assert "预览项数" in result.output

    def test_draft_show_not_found(self):
        """查看不存在的草稿"""
        result = self._run(["draft", "show", "不存在的草稿"])
        assert result.exit_code != 0
        assert "草稿不存在" in result.output

    def test_draft_delete_success(self):
        """删除草稿"""
        self._setup_sample_data()
        self._run(["draft", "save", "待删除草稿"])
        assert self._get_draft_count() == 1

        result = self._run(["draft", "delete", "待删除草稿", "--force"])
        assert result.exit_code == 0
        assert "草稿已删除" in result.output
        assert "待删除草稿" in result.output
        assert self._get_draft_count() == 0

    def test_draft_delete_with_confirm(self):
        """删除草稿需要确认"""
        self._setup_sample_data()
        self._run(["draft", "save", "确认删除草稿"])

        result = self._run(["draft", "delete", "确认删除草稿"], input_text="n\n")
        assert result.exit_code == 0
        assert "已取消删除" in result.output
        assert self._get_draft_count() == 1

        result = self._run(["draft", "delete", "确认删除草稿"], input_text="y\n")
        assert result.exit_code == 0
        assert "草稿已删除" in result.output
        assert self._get_draft_count() == 0

    def test_draft_delete_not_found(self):
        """删除不存在的草稿"""
        result = self._run(["draft", "delete", "不存在", "--force"])
        assert result.exit_code != 0
        assert "草稿不存在" in result.output

    def test_draft_persists_across_restart(self):
        """跨重启草稿仍可读取"""
        self._setup_sample_data()
        self._run(["draft", "save", "持久化测试草稿", "-d", "测试跨重启"])

        drafts_dir = self.workspace / ".patrol-archiver" / "drafts"
        draft_files = list(drafts_dir.glob("draft_*.json"))
        assert len(draft_files) == 1

        with open(draft_files[0], "r", encoding="utf-8") as f:
            draft_data = json.load(f)
        assert draft_data["name"] == "持久化测试草稿"
        assert draft_data["description"] == "测试跨重启"

        store = BatchStore(self.workspace)
        store._current_batch = None
        loaded = store.load_draft(draft_data["id"])
        assert loaded is not None
        assert loaded.name == "持久化测试草稿"
        assert len(loaded.previews) > 0

    def test_draft_restore_version_mismatch_requires_confirm(self):
        """恢复时规则版本变化需要确认"""
        self._setup_sample_data()
        self._run(["draft", "save", "版本冲突草稿"])

        self._run(["rules", "set-duplicate", "rename"])

        result = self._run(["draft", "restore", "版本冲突草稿"], input_text="n\n")
        assert result.exit_code == 0
        assert "检测到以下差异" in result.output
        assert "规则版本不匹配" in result.output
        assert "已取消恢复" in result.output

    def test_draft_restore_force_skip_confirm(self):
        """强制恢复跳过确认"""
        self._setup_sample_data()
        previews_before = self._get_batch_previews_count()
        self._run(["draft", "save", "强制恢复草稿"])

        self._run(["rules", "set-duplicate", "skip"])
        store = BatchStore(self.workspace)
        batch = store.get_current_batch()
        store.set_previews(batch, [])
        store.clear_conflicts(batch)
        assert self._get_batch_previews_count() == 0

        result = self._run(["draft", "restore", "强制恢复草稿", "--force"])
        assert result.exit_code == 0
        assert "草稿已恢复" in result.output
        assert self._get_batch_previews_count() == previews_before

    def test_draft_restore_then_dry_run_archive(self):
        """恢复草稿后可以进行 dry-run 归档"""
        self._setup_sample_data()
        self._run(["rules", "set-duplicate", "rename"])
        self._run(["draft", "save", "归档测试草稿"])

        store = BatchStore(self.workspace)
        batch = store.get_current_batch()
        store.set_previews(batch, [])
        store.clear_conflicts(batch)

        result = self._run(["draft", "restore", "归档测试草稿", "--force"])
        assert result.exit_code == 0
        assert "草稿已恢复" in result.output

        result = self._run(["archive", "--dry-run"])
        assert result.exit_code == 0
        assert "试运行" in result.output or "dry-run" in result.output or "预览" in result.output
        assert "总计" in result.output
        assert "成功" in result.output

    def test_draft_no_residue_after_delete(self):
        """删除后不会残留"""
        self._setup_sample_data()
        self._run(["draft", "save", "残留测试草稿1"])
        self._run(["draft", "save", "残留测试草稿2"])
        assert self._get_draft_count() == 2

        drafts_dir = self.workspace / ".patrol-archiver" / "drafts"
        draft_files_before = set(f.name for f in drafts_dir.glob("draft_*.json"))

        self._run(["draft", "delete", "残留测试草稿1", "--force"])
        assert self._get_draft_count() == 1

        draft_files_after = set(f.name for f in drafts_dir.glob("draft_*.json"))
        deleted_files = draft_files_before - draft_files_after
        assert len(deleted_files) == 1

        store = BatchStore(self.workspace)
        drafts = store.list_drafts()
        assert len(drafts) == 1
        assert drafts[0]["name"] == "残留测试草稿2"

    def test_draft_restore_points_change_detected(self):
        """恢复时点位变化会被检测到"""
        self._setup_sample_data()
        self._run(["draft", "save", "点位变化草稿"])

        result = self._run([
            "import", "--csv", str(self.sample / "points.csv"),
            "--use-current", "--batch-name", "同批次"
        ])

        result = self._run(["draft", "restore", "点位变化草稿"], input_text="n\n")
        assert result.exit_code == 0
        assert "点位内容已变化" in result.output
        assert "已取消恢复" in result.output

    def test_draft_restore_photos_change_detected(self):
        """恢复时照片变化会被检测到"""
        self._setup_sample_data()
        self._run(["draft", "save", "照片变化草稿"])

        alt_photos_dir = self.tmp / "alt_photos"
        alt_photos_dir.mkdir()
        src_photo = self.sample / "photos" / "P001_20260615_093000.jpg"
        import shutil
        shutil.copy2(src_photo, alt_photos_dir / "only_one_photo.jpg")

        self._run(["scan", "--dir", str(alt_photos_dir)])

        result = self._run(["draft", "restore", "照片变化草稿"], input_text="n\n")
        assert result.exit_code == 0
        assert "照片扫描结果已变化" in result.output
        assert "已取消恢复" in result.output

    def test_draft_restore_strategy_mismatch_warning(self):
        """恢复时重复策略不匹配给出警告"""
        self._setup_sample_data()
        self._run(["draft", "save", "策略草稿"])

        self._run(["rules", "set-duplicate", "skip"])

        result = self._run(["draft", "restore", "策略草稿", "--force"])
        assert result.exit_code == 0
        assert "重复策略不匹配" in result.output

    def test_draft_save_with_description(self):
        """保存草稿带描述"""
        self._setup_sample_data()
        result = self._run(["draft", "save", "带描述草稿", "-d", "这是一个详细描述"])
        assert result.exit_code == 0
        assert "草稿已保存" in result.output

        result = self._run(["draft", "show", "带描述草稿"])
        assert result.exit_code == 0
        assert "这是一个详细描述" in result.output

    def test_draft_list_shows_correct_counts(self):
        """草稿列表显示正确的计数"""
        self._setup_sample_data()
        previews_count = self._get_batch_previews_count()
        conflicts_count = self._get_batch_conflicts_count()

        self._run(["draft", "save", "计数测试草稿"])

        result = self._run(["draft", "list"])
        assert result.exit_code == 0
        assert str(previews_count) in result.output
        assert str(conflicts_count) in result.output

    def test_draft_restore_overwrites_current_state(self):
        """恢复草稿会覆盖当前状态"""
        self._setup_sample_data()
        self._run(["rules", "set-duplicate", "rename"])
        self._run(["preview"])
        self._run(["draft", "save", "恢复覆盖草稿"])

        store = BatchStore(self.workspace)
        batch = store.get_current_batch()
        previews_saved = len(batch.previews)
        conflicts_saved = len(batch.conflicts)

        store.set_previews(batch, [])
        store.clear_conflicts(batch)
        assert self._get_batch_previews_count() == 0
        assert self._get_batch_conflicts_count() == 0

        result = self._run(["draft", "restore", "恢复覆盖草稿", "--force"])
        assert result.exit_code == 0
        assert self._get_batch_previews_count() == previews_saved
        assert self._get_batch_conflicts_count() == conflicts_saved

    def test_draft_multiple_saves(self):
        """保存多个草稿"""
        self._setup_sample_data()
        for i in range(5):
            result = self._run(["draft", "save", f"草稿{i}"])
            assert result.exit_code == 0

        assert self._get_draft_count() == 5

        result = self._run(["draft", "list"])
        assert result.exit_code == 0
        for i in range(5):
            assert f"草稿{i}" in result.output
