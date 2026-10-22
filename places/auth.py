import hmac

from django.conf import settings
from django.core.cache import cache

LOGIN_ATTEMPT_LIMIT = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 900


def check_password(raw: str) -> bool:
    """Сравнение с SITE_PASSWORD за постоянное время."""
    return hmac.compare_digest(raw, settings.SITE_PASSWORD)


def _key(ip: str) -> str:
    return f"login_attempts:{ip}"


def is_locked(ip: str) -> bool:
    """True, если для этого IP превышен лимит неудачных попыток."""
    return cache.get(_key(ip), 0) >= LOGIN_ATTEMPT_LIMIT


def register_failure(ip: str) -> None:
    """Увеличить счётчик неудач. Ключ живёт всё окно."""
    key = _key(ip)
    if cache.add(key, 1, timeout=LOGIN_ATTEMPT_WINDOW_SECONDS):
        return
    try:
        cache.incr(key)
    except ValueError:
        # ключ успел истечь между add и incr — начинаем заново
        cache.add(key, 1, timeout=LOGIN_ATTEMPT_WINDOW_SECONDS)


def reset(ip: str) -> None:
    """Сбросить счётчик после успешного входа."""
    cache.delete(_key(ip))