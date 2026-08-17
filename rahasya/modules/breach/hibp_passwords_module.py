"""Free HIBP Pwned Passwords k-anonymity range lookup."""

import hashlib
import re
from typing import List

from rahasya.core.models import BreachRecord, Entity, EntityType, SourceReliability
from rahasya.modules.base import BaseModule


class HIBPPasswordsModule(BaseModule):
    name = "HIBPPasswords"
    description = "Check a password or SHA-1 hash against HIBP Pwned Passwords"
    version = "1.0.0"
    accepts = [EntityType.PASSWORD_HASH]
    produces = [EntityType.BREACH_RECORD]
    BASE_URL = "https://api.pwnedpasswords.com/range"

    @staticmethod
    def _sha1(value: str) -> str:
        cleaned = value.strip().upper()
        if re.fullmatch(r"[0-9A-F]{40}", cleaned):
            return cleaned
        return hashlib.sha1(value.encode("utf-8"), usedforsecurity=False).hexdigest().upper()

    async def execute(self, entity: Entity, scan_id: str) -> List[Entity]:
        digest = self._sha1(entity.value)
        prefix, suffix = digest[:5], digest[5:]
        response = await self.client.get(
            f"{self.BASE_URL}/{prefix}",
            headers={
                "Add-Padding": "true",
                "User-Agent": "Rahasya OSINT Platform",
            },
        )

        occurrence_count = 0
        for line in response.text.splitlines():
            candidate, separator, count = line.partition(":")
            if separator and candidate.strip().upper() == suffix:
                try:
                    occurrence_count = int(count.strip())
                except ValueError:
                    occurrence_count = 1
                break
        if occurrence_count <= 0:
            return []

        return [BreachRecord(
            value="Password hash found in HIBP Pwned Passwords",
            normalized_value=f"hibp-pwned-password:{digest}",
            source_module=self.name,
            source_reliability=SourceReliability.HIGH,
            confidence=1.0,
            metadata={"hash_prefix": prefix, "occurrence_count": occurrence_count},
            parent_entity_id=entity.id,
            depth=entity.depth + 1,
            breach_name="HIBP Pwned Passwords",
            affected_count=occurrence_count,
            severity="High",
            source_name="HIBP Pwned Passwords",
            data_types_leaked=["Password hash"],
        )]

    def is_available(self) -> bool:
        return True
