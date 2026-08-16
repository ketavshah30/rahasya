"""Atomic on-disk persistence for scan results and live progress."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from rahasya.config import settings
from rahasya.core.models import ScanResult, ScanStatus


class ScanStore:
    """Persist one JSON document per scan, with a small live-status sidecar."""

    def __init__(self, root: Optional[Path | str] = None):
        self.root = Path(root or settings.storage.scan_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def _result_path(self, scan_id: str) -> Path:
        return self.root / f"{self._safe_id(scan_id)}.json"

    def _status_path(self, scan_id: str) -> Path:
        return self.root / f"{self._safe_id(scan_id)}.status.json"

    @staticmethod
    def _safe_id(scan_id: str) -> str:
        safe = "".join(char for char in str(scan_id) if char.isalnum() or char in "-_")
        if not safe or safe != str(scan_id):
            raise ValueError("Invalid scan id")
        return safe

    @staticmethod
    def _atomic_json_write(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def save(self, result: ScanResult) -> ScanResult:
        self._atomic_json_write(self._result_path(result.scan_id), result.model_dump(mode="json"))
        return result

    def load(self, scan_id: str) -> Optional[ScanResult]:
        path = self._result_path(scan_id)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as stream:
                return ScanResult.model_validate(json.load(stream))
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def list(self) -> List[ScanResult]:
        results: List[tuple[float, ScanResult]] = []
        for path in self.root.glob("*.json"):
            if path.name.endswith(".status.json"):
                continue
            result = self.load(path.stem)
            if result is not None:
                results.append((path.stat().st_mtime, result))
        return [result for _, result in sorted(results, key=lambda item: item[0], reverse=True)]

    def delete(self, scan_id: str) -> bool:
        deleted = False
        network_path = self.root / f"{self._safe_id(scan_id)}.network.jsonl"
        for path in (self._result_path(scan_id), self._status_path(scan_id), network_path):
            if path.exists():
                path.unlink()
                deleted = True
        return deleted

    def save_status(self, scan_id: str, **status: Any) -> Dict[str, Any]:
        previous = self.load_status(scan_id) or {}
        payload = {
            **previous,
            **status,
            "scan_id": scan_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._atomic_json_write(self._status_path(scan_id), payload)
        return payload

    def load_status(self, scan_id: str) -> Optional[Dict[str, Any]]:
        path = self._status_path(scan_id)
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as stream:
                    return json.load(stream)
            except (OSError, json.JSONDecodeError):
                pass
        result = self.load(scan_id)
        if result is None:
            return None
        return {
            "scan_id": scan_id,
            "status": result.status.value,
            "entity_count": result.stats.total_entities,
            "relationship_count": result.stats.total_relationships,
            "depth": result.stats.depth_reached,
            "module": None,
            "updated_at": (result.completed_at or result.started_at or datetime.now(timezone.utc)).isoformat(),
        }

    def mark_failed(self, scan_id: str, error: str) -> ScanResult:
        result = self.load(scan_id) or ScanResult(scan_id=scan_id)
        result.status = ScanStatus.FAILED
        result.error = error
        result.completed_at = datetime.now(timezone.utc)
        self.save(result)
        self.save_status(scan_id, status=ScanStatus.FAILED.value, error=error)
        return result
