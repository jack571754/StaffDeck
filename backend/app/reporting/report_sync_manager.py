"""Report sync manager: coordinates background refreshes for task report share links."""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from app.db.database import engine
from app.db.models import ArtifactShareLink, ScheduledTask

logger = logging.getLogger(__name__)


class ReportSyncManager:
    """Coordinates background data pulling and HTML regeneration for share links."""

    def __init__(self, cooldown_seconds: float = 20.0):
        self.cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()
        self._syncing_keys: set[str] = set()
        self._last_sync_time: dict[str, float] = {}
        self._last_sync_status: dict[str, dict[str, Any]] = {}

    def is_syncing(self, key: str) -> bool:
        with self._lock:
            return key in self._syncing_keys

    def get_status(self, key: str) -> dict[str, Any]:
        with self._lock:
            active = key in self._syncing_keys
            last_t = self._last_sync_time.get(key, 0.0)
            meta = dict(self._last_sync_status.get(key, {}))
        return {
            "is_syncing": active,
            "last_sync_time": last_t,
            "seconds_since_last_sync": round(time.time() - last_t, 1) if last_t > 0 else None,
            **meta,
        }

    def should_sync(self, key: str) -> bool:
        with self._lock:
            if key in self._syncing_keys:
                return False
            last_t = self._last_sync_time.get(key, 0.0)
            return (time.time() - last_t) >= self.cooldown_seconds

    def trigger_sync(self, task_id: str, link_id: str | None = None) -> bool:
        """Launch a background refresh thread if cooldown has passed and not currently syncing."""
        sync_key = link_id or task_id
        with self._lock:
            if sync_key in self._syncing_keys:
                return False
            last_t = self._last_sync_time.get(sync_key, 0.0)
            if (time.time() - last_t) < self.cooldown_seconds:
                return False
            self._syncing_keys.add(sync_key)

        thread = threading.Thread(
            target=self._run_sync,
            args=(task_id, sync_key, link_id),
            daemon=True,
            name=f"ReportSync-{sync_key}",
        )
        thread.start()
        return True

    def _run_sync(self, task_id: str, sync_key: str, link_id: str | None) -> None:
        t0 = time.time()
        logger.info("Starting background report sync for task=%s, link=%s", task_id, link_id)
        sha256 = None
        error = None
        try:
            with Session(engine) as db:
                task = db.get(ScheduledTask, task_id)
                if not task:
                    logger.warning("Task %s not found for report sync", task_id)
                    return

                metadata = task.metadata_json if isinstance(task.metadata_json, dict) else {}
                renderer_name = metadata.get("renderer") or "sales_card"
                is_sales_task = (
                    renderer_name == "sales_card"
                    or metadata.get("task_type") == "sales_summary"
                    or task_id == "sched_ecom_realtime_30m"
                )

                if is_sales_task:
                    from app.reporting.dashboard_shops import fetch_task_all_shops
                    from app.reporting.realtime_dashboard_syncer import sync_task_artifact_report
                    from app.reporting.shop_sales_syncer import sync_task_shop_sales_report

                    all_shops = None
                    if metadata.get("shop_sales_report_enabled") is True or metadata.get("shop_sales_share_link_id"):
                        try:
                            all_shops = fetch_task_all_shops(db, task)
                            if all_shops is not None:
                                sync_task_shop_sales_report(db, task, shops=all_shops)
                        except Exception as shop_err:  # noqa: BLE001
                            logger.warning("Failed to sync shop sales report: %s", shop_err)

                    sha256 = sync_task_artifact_report(db, task, [], all_shops=all_shops)
                else:
                    from app.reporting.realtime_dashboard_syncer import sync_task_artifact_report

                    sha256 = sync_task_artifact_report(db, task, [])

                if link_id:
                    link = db.get(ArtifactShareLink, link_id)
                    if link:
                        link.updated_at = datetime.now(UTC).replace(tzinfo=None)
                        db.add(link)
                        db.commit()

        except Exception as exc:
            logger.exception("Background report sync failed for %s", sync_key)
            error = str(exc)
        finally:
            with self._lock:
                self._syncing_keys.discard(sync_key)
                self._last_sync_time[sync_key] = time.time()
                self._last_sync_status[sync_key] = {
                    "duration": round(time.time() - t0, 2),
                    "sha256": sha256,
                    "error": error,
                    "synced_at": datetime.now(UTC).isoformat(),
                }
            logger.info(
                "Finished background report sync for %s (sha256=%s, duration=%.2fs)",
                sync_key,
                sha256,
                time.time() - t0,
            )


sync_manager = ReportSyncManager(cooldown_seconds=20.0)


def find_bound_task_for_share_link(
    db: Session,
    link: ArtifactShareLink,
) -> ScheduledTask | None:
    """Find the scheduled task bound to this artifact share link."""
    tasks = db.exec(
        select(ScheduledTask).where(ScheduledTask.tenant_id == link.tenant_id)
    ).all()
    # 1. Exact match on metadata link ID
    for t in tasks:
        meta = t.metadata_json if isinstance(t.metadata_json, dict) else {}
        if (
            str(meta.get("artifact_share_link_id") or "").strip() == link.id
            or str(meta.get("shop_sales_share_link_id") or "").strip() == link.id
        ):
            return t

    # 2. Match realtime sales dashboard
    if link.path.endswith("realtime_sales_dashboard.html"):
        for t in tasks:
            if t.id == "sched_ecom_realtime_30m":
                return t
        for t in tasks:
            meta = t.metadata_json if isinstance(t.metadata_json, dict) else {}
            if meta.get("task_type") == "sales_summary":
                return t

    # 3. Match shop sales report path
    if "shop-sales-" in link.path:
        for t in tasks:
            if t.id in link.path:
                return t

    return None
