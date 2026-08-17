from enum import Enum
import re
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import AliasChoices, BaseModel, Field, ConfigDict
from pydantic import model_validator


class EntityType(str, Enum):
    PERSON = "person"
    EMAIL = "email"
    PHONE = "phone"
    USERNAME = "username"
    SOCIAL_PROFILE = "social_profile"
    URL = "url"
    PHOTO = "photo"
    IP_ADDRESS = "ip_address"
    BREACH_RECORD = "breach_record"
    DARK_WEB_MENTION = "dark_web_mention"
    LOCATION = "location"
    DOMAIN = "domain"
    LEAK_RECORD = "leak_record"
    PARTIAL_EMAIL = "partial_email"
    PARTIAL_PHONE = "partial_phone"
    COMPANY = "company"
    TIMELINE_EVENT = "timeline_event"
    PASSWORD_HASH = "password_hash"


class SourceReliability(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNVERIFIED = "unverified"


class Entity(BaseModel):
    """Base model for all discovered entities."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    entity_type: EntityType
    value: str
    normalized_value: str
    source_module: str
    scan_id: Optional[str] = None
    source_reliability: SourceReliability = SourceReliability.UNVERIFIED
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)
    parent_entity_id: Optional[str] = None
    depth: int = 0
    
    model_config = ConfigDict(frozen=False, extra='allow')


class PersonEntity(Entity):
    entity_type: EntityType = EntityType.PERSON
    name: Optional[str] = None
    aliases: List[str] = Field(default_factory=list)
    dob: Optional[str] = None
    age_range: Optional[str] = None
    location: Optional[str] = None
    gender: Optional[str] = None


class EmailEntity(Entity):
    entity_type: EntityType = EntityType.EMAIL
    address: str = ""
    domain: str = ""
    provider: Optional[str] = None
    is_disposable: bool = False

    @model_validator(mode="after")
    def populate_email_parts(self):
        if not self.address:
            self.address = self.value.strip().lower()
        if not self.domain and "@" in self.address:
            self.domain = self.address.split("@", 1)[1]
        return self


class PhoneEntity(Entity):
    entity_type: EntityType = EntityType.PHONE
    number: str = ""
    country_code: Optional[str] = None
    carrier: Optional[str] = None
    phone_type: Optional[str] = None

    @model_validator(mode="after")
    def populate_number(self):
        if not self.number:
            self.number = self.value
        return self


class _PartialValueMixin:
    """Match provider-masked recovery hints without exposing secret data."""

    normalized_value: str

    def matches_pattern(self, known_value: str) -> bool:
        masked = self.normalized_value.casefold().strip()
        known = known_value.casefold().strip()
        if not masked or not known:
            return False
        wildcarded = re.sub(r"[\u2022*·xX]+", "*", masked)
        expression = "".join(".*" if char == "*" else re.escape(char) for char in wildcarded)
        return re.fullmatch(expression, known) is not None


class PartialEmailEntity(_PartialValueMixin, Entity):
    entity_type: EntityType = EntityType.PARTIAL_EMAIL


class PartialPhoneEntity(_PartialValueMixin, Entity):
    entity_type: EntityType = EntityType.PARTIAL_PHONE


class UsernameEntity(Entity):
    entity_type: EntityType = EntityType.USERNAME
    handle: str = ""
    platforms_found: List[str] = Field(default_factory=list)
    total_sites_checked: int = 0

    @model_validator(mode="after")
    def populate_handle(self):
        if not self.handle:
            self.handle = self.value.lstrip("@")
        return self


class SocialProfileEntity(Entity):
    entity_type: EntityType = EntityType.SOCIAL_PROFILE
    url: str
    platform: str
    bio: Optional[str] = None
    followers: Optional[int] = None
    following: Optional[int] = None
    posts_count: Optional[int] = None
    created_at: Optional[datetime] = None
    profile_photo_url: Optional[str] = None
    is_verified: bool = False


class BreachRecord(Entity):
    entity_type: EntityType = EntityType.BREACH_RECORD
    breach_name: str
    breach_date: Optional[datetime] = None
    data_types_leaked: List[str] = Field(default_factory=list)
    affected_count: Optional[int] = None
    severity: Optional[str] = None
    source_name: str


class DarkWebMention(Entity):
    entity_type: EntityType = EntityType.DARK_WEB_MENTION
    source_url: str
    context_snippet: str
    search_engine: str
    is_onion: bool = False


class PhotoEntity(Entity):
    entity_type: EntityType = EntityType.PHOTO
    file_path: str
    phash: Optional[str] = None
    dhash: Optional[str] = None
    whash: Optional[str] = None
    exif_data: Dict[str, Any] = Field(default_factory=dict)
    face_count: int = 0
    gps_coords: Optional[str] = None


class LocationEntity(Entity):
    entity_type: EntityType = EntityType.LOCATION
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    source_type: Optional[str] = None


class CompanyEntity(Entity):
    entity_type: EntityType = EntityType.COMPANY
    name: Optional[str] = None
    domain: Optional[str] = None


class TimelineEvent(Entity):
    entity_type: EntityType = EntityType.TIMELINE_EVENT
    event_type: str
    occurred_at: datetime
    subject_entity_id: Optional[str] = None
    source_url: Optional[str] = None


class PersonCluster(BaseModel):
    """A resolved real-world person represented by multiple identifiers."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    entity_ids: List[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: List[str] = Field(default_factory=list)


class RelationshipType(str, Enum):
    OWNS = "OWNS"
    HAS_EMAIL = "HAS_EMAIL"
    HAS_PHONE = "HAS_PHONE"
    USES_USERNAME = "USES_USERNAME"
    HAS_PROFILE = "HAS_PROFILE"
    APPEARED_IN_BREACH = "APPEARED_IN_BREACH"
    MENTIONED_ON = "MENTIONED_ON"
    SAME_AS = "SAME_AS"
    ASSOCIATED_WITH = "ASSOCIATED_WITH"
    TAKEN_AT = "TAKEN_AT"
    WORKS_AT = "WORKS_AT"
    KNOWS = "KNOWS"
    LINKED_TO = "LINKED_TO"
    SHARES_RECOVERY = "SHARES_RECOVERY"
    PARENT_OF = "PARENT_OF"
    SIBLING_OF = "SIBLING_OF"
    SPOUSE_OF = "SPOUSE_OF"
    WORKS_WITH = "WORKS_WITH"
    MENTIONS = "MENTIONS"
    ALT_ACCOUNT_OF = "ALT_ACCOUNT_OF"
    EMPLOYED_AT = "EMPLOYED_AT"
    LIKELY_SAME = "LIKELY_SAME"


class Relationship(BaseModel):
    """Represents a relationship between two entities."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    source_id: str
    target_id: str
    relationship_type: RelationshipType = Field(
        validation_alias=AliasChoices("relationship_type", "type")
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source_module: str
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


class ScanRequest(BaseModel):
    """Initial parameters for an OSINT scan."""
    name: Optional[str] = Field(default=None, validation_alias=AliasChoices("name", "target_name"))
    email: Optional[str] = Field(default=None, validation_alias=AliasChoices("email", "target_email"))
    phone: Optional[str] = Field(default=None, validation_alias=AliasChoices("phone", "target_phone"))
    username: Optional[str] = Field(default=None, validation_alias=AliasChoices("username", "target_username"))
    photo_path: Optional[str] = None
    dob: Optional[str] = None
    age_range: Optional[str] = None
    location: Optional[str] = None
    max_depth: int = 3
    max_entities: int = 500

    model_config = ConfigDict(populate_by_name=True)

    @property
    def target_name(self) -> Optional[str]:
        return self.name

    @property
    def target_email(self) -> Optional[str]:
        return self.email

    @property
    def target_phone(self) -> Optional[str]:
        return self.phone

    @property
    def target_username(self) -> Optional[str]:
        return self.username


class ScanStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ScanStats(BaseModel):
    """Statistics collected during a scan."""
    total_entities: int = 0
    by_type: Dict[str, int] = Field(default_factory=dict)
    total_relationships: int = 0
    modules_run: int = 0
    depth_reached: int = 0
    duration_seconds: float = 0.0


class ScanResult(BaseModel):
    """Final output of an OSINT scan."""
    scan_id: str
    status: ScanStatus = ScanStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    entities: List[Entity] = Field(default_factory=list)
    relationships: List[Relationship] = Field(default_factory=list)
    stats: ScanStats = Field(default_factory=ScanStats)
    request: Optional[ScanRequest] = None
    error: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_counts(cls, data):
        if isinstance(data, dict):
            stats = data.get("stats") or {}
            if "entities_found" in data:
                stats["total_entities"] = data["entities_found"]
            if "relationships_found" in data:
                stats["total_relationships"] = data["relationships_found"]
            if stats:
                data["stats"] = stats
            if isinstance(data.get("status"), str):
                data["status"] = data["status"].upper()
        return data

    @property
    def entities_found(self) -> int:
        return self.stats.total_entities

    @property
    def relationships_found(self) -> int:
        return self.stats.total_relationships
