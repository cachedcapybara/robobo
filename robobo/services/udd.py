"""UDD（Ultimate Debian Database）镜像查询。

使用 asyncpg 连接池：进程启动时惰性建池，UDD 短暂不可用时自动重建，
不会因为中间件挂掉而让 bot 起不来。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import asyncpg

log = logging.getLogger(__name__)

_VERSIONS_SQL = """
    SELECT ps.release, ps.version
      FROM packages_summary ps
      LEFT JOIN releases r
        ON r.release = ps.release
       AND r.distribution = 'debian'
     WHERE ps.package = $1
     ORDER BY r.sort ASC NULLS LAST, ps.release ASC
"""


class UddError(RuntimeError):
    """UDD 查询失败。"""


class UddUnavailable(UddError):
    """没有配置 UDD，或连接池尚未建立。"""


@dataclass(frozen=True, slots=True)
class PackageVersion:
    release: str
    version: str


class UddClient:
    def __init__(self, dsn: str | None) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return self._dsn is not None

    async def versions(self, package: str) -> list[PackageVersion]:
        pool = await self._ensure_pool()
        try:
            rows = await pool.fetch(_VERSIONS_SQL, package)
        except (asyncpg.PostgresError, OSError, asyncio.TimeoutError) as exc:
            await self._reset()
            raise UddError(str(exc)) from exc

        return [PackageVersion(release=r["release"], version=r["version"]) for r in rows]

    async def close(self) -> None:
        await self._reset()

    async def _ensure_pool(self) -> asyncpg.Pool:
        if not self._dsn:
            raise UddUnavailable("未配置 UDD_DSN")
        if self._pool is not None:
            return self._pool

        async with self._lock:
            if self._pool is None:
                try:
                    self._pool = await asyncpg.create_pool(
                        dsn=self._dsn,
                        min_size=1,
                        max_size=4,
                        timeout=10,
                        command_timeout=10,
                    )
                except (OSError, asyncpg.PostgresError, asyncio.TimeoutError) as exc:
                    raise UddError(f"连接 UDD 失败：{exc}") from exc
                log.info("UDD connection pool created")

        assert self._pool is not None
        return self._pool

    async def _reset(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            try:
                await pool.close()
            except Exception:
                log.debug("Failed to close UDD pool", exc_info=True)
