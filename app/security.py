import hashlib
import hmac

from app.config import get_settings


def signature_is_valid(raw_body: bytes, signature: str | None) -> bool:
    settings = get_settings()
    if not settings.webhook_signature_required:
        return True
    if not signature or not settings.pseudogram_api_key:
        return False
    expected = "sha256=" + hmac.new(
        settings.pseudogram_api_key.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

