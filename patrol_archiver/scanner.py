from __future__ import annotations

import re
import struct
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .models import Photo


class PhotoScanner:
    def __init__(self, workspace: Path, allowed_extensions: Optional[List[str]] = None):
        self.workspace = Path(workspace).resolve()
        self.allowed_extensions = [
            ext.lower() if ext.startswith(".") else f".{ext.lower()}"
            for ext in (allowed_extensions or [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"])
        ]

    def scan_photos(
        self,
        photo_dir: Path,
        recursive: bool = True,
    ) -> Tuple[List[Photo], List[str]]:
        photo_dir = Path(photo_dir).resolve()
        photos: List[Photo] = []
        skipped: List[str] = []

        if not photo_dir.exists():
            return photos, [f"照片目录不存在: {photo_dir}"]

        if not photo_dir.is_dir():
            return photos, [f"路径不是目录: {photo_dir}"]

        pattern = "**/*" if recursive else "*"
        for path in sorted(photo_dir.glob(pattern)):
            if not path.is_file():
                continue

            ext = path.suffix.lower()
            if ext not in self.allowed_extensions:
                skipped.append(f"跳过不支持的文件类型: {path} (扩展名: {ext})")
                continue

            try:
                photo = Photo.from_path(path)
                photo.taken_at = self._extract_taken_time(path)
                photo.point_id = self._extract_point_id(path.name)
                photo.camera = self._extract_camera(path)
                photos.append(photo)
            except Exception as e:
                skipped.append(f"无法读取文件 {path}: {e}")

        return photos, skipped

    def _extract_taken_time(self, path: Path) -> Optional[datetime]:
        try:
            taken_at = self._read_exif_datetime(path)
            if taken_at:
                return taken_at
        except Exception:
            pass

        try:
            taken_at = self._parse_datetime_from_filename(path.name)
            if taken_at:
                return taken_at
        except Exception:
            pass

        stat = path.stat()
        return datetime.fromtimestamp(min(stat.st_mtime, stat.st_ctime))

    def _read_exif_datetime(self, path: Path) -> Optional[datetime]:
        ext = path.suffix.lower()
        if ext not in (".jpg", ".jpeg", ".tiff"):
            return None

        try:
            with open(path, "rb") as f:
                data = f.read(1024 * 1024)

            if not data.startswith(b"\xff\xd8"):
                return None

            offset = 2
            while offset < len(data) - 4:
                if data[offset] != 0xFF:
                    break

                marker = data[offset + 1]
                if marker in (0xD8, 0xD9):
                    offset += 2
                    continue

                segment_len = struct.unpack(">H", data[offset + 2:offset + 4])[0]
                segment_end = offset + 2 + segment_len

                if marker == 0xE1:
                    if data[offset + 4:offset + 10] == b"Exif\x00\x00":
                        exif_data = data[offset + 10:segment_end]
                        dt_str = self._parse_exif_datetime_field(exif_data)
                        if dt_str:
                            return datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")

                offset = segment_end
        except Exception:
            pass

        return None

    @staticmethod
    def _parse_exif_datetime_field(exif_data: bytes) -> Optional[str]:
        try:
            byte_order = "<" if exif_data[:2] == b"II" else ">"
            offset = struct.unpack(byte_order + "I", exif_data[4:8])[0]

            num_entries = struct.unpack(byte_order + "H", exif_data[offset:offset + 2])[0]
            base = offset + 2

            for i in range(num_entries):
                entry_offset = base + i * 12
                tag = struct.unpack(byte_order + "H", exif_data[entry_offset:entry_offset + 2])[0]
                if tag in (0x0132, 0x9003, 0x9004):
                    value_offset = struct.unpack(
                        byte_order + "I",
                        exif_data[entry_offset + 8:entry_offset + 12]
                    )[0]
                    dt_bytes = exif_data[value_offset:value_offset + 19]
                    return dt_bytes.decode("ascii", errors="ignore").strip("\x00")
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_datetime_from_filename(filename: str) -> Optional[datetime]:
        patterns = [
            r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})[-_ ]?(\d{2})[-_:]?(\d{2})[-_:]?(\d{2})",
            r"(\d{8})[-_ ]?(\d{6})",
            r"IMG[_-](\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})",
            r"DSC[_-](\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})",
        ]

        for pattern in patterns:
            match = re.search(pattern, filename)
            if match:
                groups = match.groups()
                if len(groups) == 6:
                    try:
                        return datetime(*(int(g) for g in groups))
                    except (ValueError, TypeError):
                        pass
                elif len(groups) == 2:
                    try:
                        date_str, time_str = groups
                        return datetime(
                            int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]),
                            int(time_str[:2]), int(time_str[2:4]), int(time_str[4:6]),
                        )
                    except (ValueError, TypeError):
                        pass
        return None

    @staticmethod
    def _extract_point_id(filename: str) -> Optional[str]:
        patterns = [
            r"(?:^|[_-])([A-Za-z]\d{3,})(?:[_.-]|$)",
            r"(?:^|[_-])([A-Za-z]\d{1,2})(?:[_.-]|$)",
            r"(?:^|[_-])([Pp]\d{3,})(?:[_.-]|$)",
            r"(?:^|[_-])([Pp]\d{1,2})(?:[_.-]|$)",
            r"(?:point|pt|点位)[-_]?([A-Za-z]?\d+)",
        ]

        for pattern in patterns:
            match = re.search(pattern, filename)
            if match:
                raw_id = match.group(1).upper()
                if re.match(r"^\d+$", raw_id):
                    raw_id = f"P{raw_id}"
                return raw_id
        return None

    @staticmethod
    def _extract_camera(path: Path) -> str:
        return ""

    @staticmethod
    def find_duplicates(photos: List[Photo]) -> Dict[str, List[Photo]]:
        hash_map: Dict[str, List[Photo]] = {}
        for photo in photos:
            hash_map.setdefault(photo.file_hash, []).append(photo)
        return {h: p for h, p in hash_map.items() if len(p) > 1}

    @staticmethod
    def find_name_conflicts(photos: List[Photo]) -> Dict[str, List[Photo]]:
        name_map: Dict[str, List[Photo]] = {}
        for photo in photos:
            name_map.setdefault(photo.file_name.lower(), []).append(photo)
        return {n: p for n, p in name_map.items() if len(p) > 1}
