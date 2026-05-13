from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from models import QuotaReport
from utils import atomic_write_json, read_json, utc_timestamp


NOTIFY_TRANSITIONS = {
    ("ok", "warning"),
    ("ok", "critical"),
    ("warning", "critical"),
    ("warning", "ok"),
    ("critical", "ok"),
    ("error", "ok"),
    ("ok", "error"),
}


class QuotaStateStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.notify_targets_path = data_dir / "notify_targets.json"
        self.last_state_path = data_dir / "last_quota_state.json"
        self._lock = asyncio.Lock()
        self._initialized = self.last_state_path.exists()

    async def list_notify_targets(self) -> list[str]:
        async with self._lock:
            data = read_json(self.notify_targets_path, {"targets": []})
            targets = data.get("targets", []) if isinstance(data, dict) else []
            return sorted({str(item) for item in targets if str(item).strip()})

    async def add_notify_target(self, target: str) -> None:
        async with self._lock:
            data = read_json(self.notify_targets_path, {"targets": []})
            targets = set(data.get("targets", []) if isinstance(data, dict) else [])
            targets.add(target)
            atomic_write_json(self.notify_targets_path, {"targets": sorted(targets), "updated_at": utc_timestamp()})

    async def remove_notify_target(self, target: str) -> None:
        async with self._lock:
            data = read_json(self.notify_targets_path, {"targets": []})
            targets = set(data.get("targets", []) if isinstance(data, dict) else [])
            targets.discard(target)
            atomic_write_json(self.notify_targets_path, {"targets": sorted(targets), "updated_at": utc_timestamp()})

    async def diff_and_save(self, report: QuotaReport) -> list[dict[str, Any]]:
        async with self._lock:
            previous = read_json(self.last_state_path, {"items": {}})
            previous_items = previous.get("items", {}) if isinstance(previous, dict) else {}
            current_items: dict[str, str] = {}
            changes: list[dict[str, Any]] = []

            for provider in report.providers:
                for account in provider.accounts:
                    for item in account.items:
                        key = item.state_key(provider.type, account.id)
                        old_status = str(previous_items.get(key, "ok"))
                        new_status = item.status
                        current_items[key] = new_status
                        if self._initialized and old_status != new_status and (old_status, new_status) in NOTIFY_TRANSITIONS:
                            changes.append(
                                {
                                    "key": key,
                                    "from": old_status,
                                    "to": new_status,
                                    "provider_name": provider.name,
                                    "provider_type": provider.type,
                                    "account_id": account.id,
                                    "account_name": account.display_name,
                                    "item": item.as_dict(),
                                }
                            )

            atomic_write_json(self.last_state_path, {"items": current_items, "updated_at": utc_timestamp()})
            self._initialized = True
            return changes
