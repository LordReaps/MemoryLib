from django.conf import settings
from django.shortcuts import redirect
from urllib.parse import quote

EXEMPT_PREFIXES = (
    "/login/",
    "/logout/",
    "/static/",
    "/media/",
)


class SitePasswordMiddleware:
    """Пускает дальше только после ввода общего пароля."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.session.get("authenticated"):
            return self.get_response(request)

        if request.path.startswith(EXEMPT_PREFIXES):
            return self.get_response(request)

        return redirect(f"/login/?next={quote(request.get_full_path())}")