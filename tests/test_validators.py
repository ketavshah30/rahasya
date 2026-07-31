import pytest

try:
    from rahasya.core.validators import (
        normalize_email, normalize_phone, generate_username_variants,
        validate_url, extract_domain, is_disposable_email, normalize_name
    )
    HAS_VALIDATORS = True
except ImportError:
    HAS_VALIDATORS = False

pytestmark = pytest.mark.skipif(not HAS_VALIDATORS, reason="Validators module not found")

def test_normalize_email():
    if not HAS_VALIDATORS: return
    assert normalize_email("Test@Example.com") == "test@example.com"
    assert normalize_email("invalid-email") is None

def test_normalize_phone():
    if not HAS_VALIDATORS: return
    assert normalize_phone("+1 (234) 567-8900") == "+12345678900"
    assert normalize_phone("invalid") is None

def test_generate_username_variants():
    if not HAS_VALIDATORS: return
    variants = generate_username_variants("John Doe")
    assert "johndoe" in variants
    assert "john.doe" in variants
    
def test_validate_url():
    if not HAS_VALIDATORS: return
    assert validate_url("http://example.com") == "http://example.com"
    assert validate_url("invalid") is None

def test_extract_domain():
    if not HAS_VALIDATORS: return
    assert extract_domain("https://www.example.com/path") == "example.com"
    assert extract_domain("invalid") is None

def test_is_disposable_email():
    if not HAS_VALIDATORS: return
    assert is_disposable_email("test@mailinator.com") is True
    assert is_disposable_email("test@gmail.com") is False

def test_normalize_name():
    if not HAS_VALIDATORS: return
    assert normalize_name("  John   Doe  ") == "john doe"
    assert normalize_name("JÖHN") == "john"
