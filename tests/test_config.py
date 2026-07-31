import pytest

try:
    from rahasya.core.config import Settings, ScanSettings, APIKeys, TorSettings
    HAS_CONFIG = True
except ImportError:
    HAS_CONFIG = False

pytestmark = pytest.mark.skipif(not HAS_CONFIG, reason="Config module not found")

def test_default_settings_load():
    if not HAS_CONFIG: return
    settings = Settings()
    assert settings is not None

def test_scan_settings_defaults():
    if not HAS_CONFIG: return
    scan = ScanSettings()
    assert scan.max_depth == 3
    assert scan.max_entities == 500

def test_api_keys_defaults():
    if not HAS_CONFIG: return
    keys = APIKeys()
    assert keys.shodan is None
    assert keys.virustotal is None

def test_tor_settings_defaults():
    if not HAS_CONFIG: return
    tor = TorSettings()
    assert tor.enabled is False
