from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field, ConfigDict


class EntityType(str, Enum):
    PERSON = "PERSON"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    USERNAME = "USERNAME"
    SOCIAL_PROFILE = "SOCIAL_PROFILE"
    URL = "URL"
    PHOTO = "PHOTO"
    IP_ADDRESS = "IP_ADDRESS"
    BREACH_RECORD = "BREACH_RECORD"
    DARK_WEB_MENTION = "DARK_WEB_MENTION"
    LOCATION = "LOCATION"
    DOMAIN = "DOMAIN"
    LEAK_RECORD = "LEAK_RECORD"


class SourceReliability(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNVERIFIED = "UNVERIFIED"


class Entity(BaseModel):
    """Base model for all discovered entities."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    entity_type: EntityType
    value: str
    normalized_value: str
    source_module: str
    source_reliability: SourceReliability = SourceReliability.UNVERIFIED
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
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
    address: str
    domain: str
    provider: Optional[str] = None
    is_disposable: bool = False


class PhoneEntity(Entity):
    entity_type: EntityType = EntityType.PHONE
    number: str
    country_code: Optional[str] = None
    carrier: Optional[str] = None
    phone_type: Optional[str] = None


class UsernameEntity(Entity):
    entity_type: EntityType = EntityType.USERNAME
    handle: str
    platforms_found: List[str] = Field(default_factory=list)
    total_sites_checked: int = 0


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


class RelationshipType(str, Enum):
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


class Relationship(BaseModel):
    """Represents a relationship between two entities."""
    source_id: str
    target_id: str
    relationship_type: RelationshipType
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source_module: str
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ScanRequest(BaseModel):
    """Initial parameters for an OSINT scan."""
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    username: Optional[str] = None
    photo_path: Optional[str] = None
    dob: Optional[str] = None
    age_range: Optional[str] = None
    location: Optional[str] = None


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
