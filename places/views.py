from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from .models import ChangeRequest
from .services import ServiceOperationError, approve_change_request, reject_change_request


@require_POST
def change_request_approve(request, pk):
    change_request = get_object_or_404(ChangeRequest, pk=pk)
    try:
        approve_change_request(change_request.pk, reviewed_by="admin")
    except ServiceOperationError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "status": "approved", "id": pk})


@require_POST
def change_request_reject(request, pk):
    change_request = get_object_or_404(ChangeRequest, pk=pk)
    try:
        reject_change_request(change_request.pk, reviewed_by="admin")
    except ServiceOperationError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "status": "rejected", "id": pk})
