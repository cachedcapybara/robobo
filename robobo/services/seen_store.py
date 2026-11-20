"""安全公告的持久化状态：已见 ID 与各数据源的 ETag。

状态写在单个 JSON 文件里，用「临时文件 + os.replace」原子替换，并保留一份
``.bak``。这样进程崩溃/重启不会丢已见记录，避免重复推送或漏推。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

STATE_VERSION = 1


@dataclass(slots=True)
class SeenStore:
    """已见公告 ID 与 ETag 的内存视图 + 落盘。"""

    path: str
    seen: dict[str, str] = field(default_factory=dict)
    etag: dict[str, str] = field(default_factory=dict)
    version: int = STATE_VERSION

    @property
    def empty(self) -> bool:
        return not self.seen

    @classmethod
    def load(cls, path: str) -> SeenStore:
        """读取状态文件；不存在或损坏时回退 ``.bak``，再不行就空状态。"""
        for candidate in (path, f"{path}.bak"):
            raw = _read_json(candidate)
            if raw is None:
                continue
            try:
                return cls(
                    path=path,
                    seen=dict(raw.get("seen") or {}),
                    etag=dict(raw.get("etag") or {}),
                    version=int(raw.get("version", STATE_VERSION)),
                )
            except (TypeError, ValueError):
                log.warning("状态文件 %s 格式异常，尝试下一个", candidate)
        if os.path.exists(path) or os.path.exists(f"{path}.bak"):
            log.warning("状态文件不可用，将重新建立基线：%s", path)
        return cls(path=path)

    def has(self, advisory_id: str) -> bool:
        return advisory_id in self.seen

    def mark(self, advisory_id: str, date: str) -> None:
        self.seen[advisory_id] = date

    def get_etag(self, key: str) -> str | None:
        return self.etag.get(key)

    def set_etag(self, key: str, etag: str | None) -> None:
        if etag:
            self.etag[key] = etag
        else:
            self.etag.pop(key, None)

    def save(self) -> None:
        """原子写入，并更新 ``.bak``。"""
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)

        payload = json.dumps(
            {
                "version": self.version,
                "seen": self.seen,
                "etag": self.etag,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)
        shutil.copy2(self.path, f"{self.path}.bak")


def _read_json(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("读取状态文件 %s 失败：%s", path, exc)
        return None
    return data if isinstance(data, dict) else None
