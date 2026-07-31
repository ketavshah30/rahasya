import re
import unicodedata
import urllib.parse
from typing import List, Optional
try:
    import phonenumbers
except ImportError:
    phonenumbers = None


def normalize_email(email: str) -> Optional[str]:
    """Clean and validate an email address."""
    cleaned = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cleaned):
        return None
    return cleaned


def normalize_phone(phone: str, country_code: str = "US") -> Optional[str]:
    """Normalizes a phone number to E.164 format."""
    if not phonenumbers:
        # Fallback basic normalization if library not installed
        cleaned = re.sub(r'[^\d+]', '', phone)
        digits = re.sub(r"\D", "", cleaned)
        if len(digits) < 7:
            return None
        if not cleaned.startswith('+'):
            cleaned = '+' + cleaned
        return cleaned

    try:
        parsed = phonenumbers.parse(phone, country_code)
        if not phonenumbers.is_valid_number(parsed):
            return None
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except Exception:
        return None


def generate_username_variants(name: str) -> List[str]:
    """Generates common username variants from a given name."""
    name = name.lower().strip()
    parts = re.split(r'[\s_.-]+', name)
    parts = [p for p in parts if p]
    
    if not parts:
        return []
        
    variants = set()
    variants.add("".join(parts))
    variants.add("_".join(parts))
    variants.add(".".join(parts))
    
    if len(parts) >= 2:
        first, last = parts[0], parts[-1]
        variants.add(f"{first}{last}")
        variants.add(f"{first}_{last}")
        variants.add(f"{first}.{last}")
        variants.add(f"{first[0]}{last}")
        variants.add(f"{last}{first}")
        variants.add(f"{first}{last[0]}")
        
    return list(variants)


def validate_url(url: str) -> Optional[str]:
    """Validate and normalize a URL."""
    try:
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        parsed = urllib.parse.urlparse(url)
        if not parsed.netloc or "." not in parsed.netloc:
            return None
        
        return urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
        )
    except Exception:
        return None


def extract_domain(url: str) -> Optional[str]:
    """Extracts the domain from a URL."""
    norm_url = validate_url(url)
    if not norm_url:
        return None
    try:
        parsed = urllib.parse.urlparse(norm_url)
        netloc = parsed.netloc.split(':')[0]
        if netloc.startswith('www.'):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return None


DISPOSABLE_DOMAINS = {"mailinator.com", "10minutemail.com", "temp-mail.org", "guerrillamail.com"}

def is_disposable_email(email: str) -> bool:
    """Checks if an email is disposable against a known list."""
    try:
        normalized = normalize_email(email)
        if not normalized:
            return False
        domain = normalized.split("@", 1)[1]
        return domain in DISPOSABLE_DOMAINS
    except Exception:
        return False


def normalize_name(name: str) -> str:
    """Normalize whitespace, accents, and casing for a person's name."""
    cleaned = unicodedata.normalize("NFKD", name)
    cleaned = cleaned.encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9\s'.-]", "", cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned.strip())
    return cleaned.lower()
