import pytest
from datetime import datetime
from pydantic import BaseModel
from unittest.mock import Mock, MagicMock

# Assuming these exist in the user's project
# We will mock/import what we can based on typical structures

@pytest.fixture
def settings():
    # Mocks for testing if real module is missing
    try:
        from rahasya.core.config import Settings
        return Settings(database_url="sqlite:///:memory:")
    except ImportError:
        return Mock()

@pytest.fixture
def sample_scan_request():
    try:
        from rahasya.core.models import ScanRequest
        return ScanRequest(
            target_name="John Doe",
            target_email="john.doe@example.com",
            target_phone="+1234567890",
            target_username="johndoe",
            max_depth=3,
            max_entities=500
        )
    except ImportError:
        return Mock()

@pytest.fixture
def sample_entity():
    try:
        from rahasya.core.models import PersonEntity, EntityType
        return PersonEntity(
            entity_type=EntityType.PERSON,
            value="John Doe",
            normalized_value="john doe",
            source_module="test_module"
        )
    except ImportError:
        return Mock()

@pytest.fixture
def sample_entities():
    try:
        from rahasya.core.models import PersonEntity, EmailEntity, PhoneEntity, UsernameEntity, EntityType
        return [
            PersonEntity(
                entity_type=EntityType.PERSON,
                value="John Doe",
                normalized_value="john doe",
                source_module="test_module"
            ),
            EmailEntity(
                entity_type=EntityType.EMAIL,
                value="john.doe@example.com",
                normalized_value="john.doe@example.com",
                source_module="test_module"
            ),
            PhoneEntity(
                entity_type=EntityType.PHONE,
                value="+1234567890",
                normalized_value="+1234567890",
                source_module="test_module"
            ),
            UsernameEntity(
                entity_type=EntityType.USERNAME,
                value="johndoe",
                normalized_value="johndoe",
                source_module="test_module"
            )
        ]
    except ImportError:
        return []
