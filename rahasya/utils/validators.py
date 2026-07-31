import re
import urllib.parse
from typing import Tuple, List, Optional
try:
    import phonenumbers
except ImportError:
    phonenumbers = None


def normalize_email(email: str) -> Tuple[str, str]:
    """Cleans an email address and extracts its domain.
    Returns (cleaned_email, domain).
    """
    cleaned = email.strip().lower()
    if "@" not in cleaned:
        raise ValueError(f"Invalid email address: {email}")
    domain = cleaned.split("@")[1]
    return cleaned, domain


def normalize_phone(phone: str, country_code: str = "US") -> str:
    """Normalizes a phone number to E.164 format."""
    if not phonenumbers:
        # Fallback basic normalization if library not installed
        cleaned = re.sub(r'[^\d+]', '', phone)
        if not cleaned.startswith('+'):
            cleaned = '+' + cleaned
        return cleaned

    try:
        parsed = phonenumbers.parse(phone, country_code)
        if not phonenumbers.is_valid_number(parsed):
            raise ValueError(f"Invalid phone number: {phone}")
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except Exception as e:
        raise ValueError(f"Error parsing phone {phone}: {e}")


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


def validate_url(url: str) -> Tuple[bool, str]:
    """Validates and normalizes a URL. Returns (is_valid, normalized_url)."""
    try:
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        parsed = urllib.parse.urlparse(url)
        if not parsed.netloc:
            return False, url
        
        normalized = urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
        )
        return True, normalized
    except Exception:
        return False, url


def extract_domain(url: str) -> Optional[str]:
    """Extracts the domain from a URL."""
    is_valid, norm_url = validate_url(url)
    if not is_valid:
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
        _, domain = normalize_email(email)
        return domain in DISPOSABLE_DOMAINS
    except Exception:
        return False


def normalize_name(name: str) -> str:
    """Cleans and title-cases a person's name."""
    cleaned = re.sub(r'\s+', ' ', name.strip())
    return cleaned.title()
