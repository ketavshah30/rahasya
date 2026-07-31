"""Compatibility exports for validation helpers."""

from rahasya.utils.validators import (
    extract_domain,
    generate_username_variants,
    is_disposable_email,
    normalize_email,
    normalize_name,
    normalize_phone,
    validate_url,
)

__all__ = [
    "extract_domain",
    "generate_username_variants",
    "is_disposable_email",
    "normalize_email",
    "normalize_name",
    "normalize_phone",
    "validate_url",
]
