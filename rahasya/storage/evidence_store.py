"""Append module observations once, separately from frequently updated snapshots."""

import json
import os
from pathlib import Path

from rahasya.storage.scan_store import ScanStore


class EvidenceStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, scan_id):
        return self.root / f"{ScanStore._safe_id(scan_id)}.evidence.jsonl"

    def append_batch(self, scan_id, action, module, subject_id, results, selected):
        """Return byte references for selected objects, retaining every observation.

        A scan has one sequential agent runtime/writer. Readers access only
        references published after this batch is flushed and synced.
        """
        path = self.path(scan_id)
        wanted = {id(entity) for entity in selected}
        references = {}
        with path.open("ab") as stream:
            for entity in results:
                start = stream.tell()
                record = {"action": action, "module": module, "subject_id": subject_id,
                          "entity": entity.model_dump(mode="json")}
                stream.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
                if id(entity) in wanted:
                    references[id(entity)] = {"file": path.name, "offset": start, "entity_id": entity.id}
            stream.flush()
            os.fsync(stream.fileno())
        return references

    def read(self, scan_id, offset):
        """Fetch one archived record without loading the full scan output."""
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("Invalid evidence offset")
        with self.path(scan_id).open("rb") as stream:
            stream.seek(offset)
            return json.loads(stream.readline())
