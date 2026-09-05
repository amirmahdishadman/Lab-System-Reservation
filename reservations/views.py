import json
import mimetypes
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_http_methods

from .forms import AdminUserCreateForm, AdminUserUpdateForm, ProfileForm, SystemForm
from .models import Reservation, System, User
from .services import ReservationConflict, cancel_reservation, save_reservation


lab_tz = ZoneInfo(settings.TIME_ZONE)


def _staff(user):
    return user.is_authenticated and user.is_staff


staff_required = user_passes_test(_staff, login_url="login")


def _parse_time(value):
    parsed = parse_datetime(value or "")
    if parsed is None:
        raise ValidationError("Use a valid ISO-8601 date and time.")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, lab_tz)
    return parsed


def _calendar_time(value):
    # FullCalendar receives a wall-clock value so every browser shows the lab's configured time.
    return timezone.localtime(value, lab_tz).replace(tzinfo=None).isoformat(timespec="seconds")


def _body(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError as exc:
        raise ValidationError("Request body must be valid JSON.") from exc


def _error(message, status=400):
    if hasattr(message, "messages"):
        message = " ".join(message.messages)
    return JsonResponse({"error": str(message)}, status=status)


def _event(reservation, actor):
    editable = actor.is_staff or (
        reservation.owner_id == actor.pk
        and reservation.status == Reservation.Status.ACTIVE
        and reservation.start > timezone.now()
    )
    return {
        "id": str(reservation.pk),
        "title": f"{reservation.system.name} · {reservation.owner}",
        "start": _calendar_time(reservation.start),
        "end": _calendar_time(reservation.end),
        "backgroundColor": reservation.system.color,
        "borderColor": reservation.system.color,
        "editable": editable,
        "extendedProps": {
            "systemId": reservation.system_id,
            "systemName": reservation.system.name,
            "ownerId": reservation.owner_id,
            "ownerName": str(reservation.owner),
            "avatarUrl": reservation.owner.avatar_url,
            "purpose": reservation.purpose,
            "notes": reservation.notes,
            "canEdit": editable,
        },
    }


@login_required
def calendar_view(request):
    return render(request, "reservations/calendar.html", {
        "systems": System.objects.filter(is_active=True),
        "users": User.objects.filter(is_active=True).order_by("display_name", "username") if request.user.is_staff else [],
        "lab_timezone": settings.TIME_ZONE,
    })


@login_required
@require_http_methods(["GET", "POST"])
def reservation_collection(request):
    if request.method == "GET":
        try:
            start = _parse_time(request.GET.get("start"))
            end = _parse_time(request.GET.get("end"))
        except ValidationError as exc:
            return _error(exc)
        reservations = Reservation.objects.filter(
            status=Reservation.Status.ACTIVE, start__lt=end, end__gt=start
        ).select_related("system", "owner")
        system_id = request.GET.get("system")
        if system_id:
            reservations = reservations.filter(system_id=system_id)
        return JsonResponse([_event(item, request.user) for item in reservations], safe=False)

    try:
        data = _body(request)
        owner = request.user
        if request.user.is_staff and data.get("ownerId"):
            owner = get_object_or_404(User, pk=data["ownerId"], is_active=True)
        reservation = save_reservation(
            actor=request.user,
            owner=owner,
            system_id=data.get("systemId"),
            start=_parse_time(data.get("start")),
            end=_parse_time(data.get("end")),
            purpose=data.get("purpose", ""),
            notes=data.get("notes", ""),
        )
        reservation = Reservation.objects.select_related("system", "owner").get(pk=reservation.pk)
        return JsonResponse(_event(reservation, request.user), status=201)
    except ReservationConflict as exc:
        return _error(exc, 409)
    except (ValidationError, ValueError, TypeError) as exc:
        return _error(exc)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def reservation_detail(request, reservation_id):
    try:
        if request.method == "DELETE":
            cancel_reservation(actor=request.user, reservation_id=reservation_id)
            return JsonResponse({}, status=204)

        data = _body(request)
        current = get_object_or_404(Reservation, pk=reservation_id)
        owner = current.owner
        if request.user.is_staff and data.get("ownerId"):
            owner = get_object_or_404(User, pk=data["ownerId"], is_active=True)
        reservation = save_reservation(
            actor=request.user,
            reservation_id=reservation_id,
            owner=owner,
            system_id=data.get("systemId", current.system_id),
            start=_parse_time(data.get("start")),
            end=_parse_time(data.get("end")),
            purpose=data.get("purpose", current.purpose),
            notes=data.get("notes", current.notes),
        )
        reservation = Reservation.objects.select_related("system", "owner").get(pk=reservation.pk)
        return JsonResponse(_event(reservation, request.user))
    except Reservation.DoesNotExist:
        return _error("Reservation not found.", 404)
    except ReservationConflict as exc:
        return _error(exc, 409)
    except PermissionError as exc:
        return _error(exc, 403)
    except (ValidationError, ValueError, TypeError) as exc:
        return _error(exc)


@login_required
def profile_view(request):
    form = ProfileForm(request.POST or None, request.FILES or None, instance=request.user)
    password_form = PasswordChangeForm(request.user, request.POST or None, prefix="password")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "profile" and form.is_valid():
            form.save()
            messages.success(request, "Profile updated.")
            return redirect("profile")
        if action == "password" and password_form.is_valid():
            user = password_form.save()
            update_session_auth_hash(request, user)
            messages.success(request, "Password changed.")
            return redirect("profile")
    return render(request, "reservations/profile.html", {"form": form, "password_form": password_form})


@login_required
@require_GET
def user_avatar(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    if not user.avatar:
        raise Http404
    try:
        content_type = mimetypes.guess_type(user.avatar.name)[0] or "image/webp"
        return FileResponse(user.avatar.open("rb"), content_type=content_type)
    except FileNotFoundError as exc:
        raise Http404 from exc


@staff_required
def manage_users(request):
    return render(request, "reservations/manage/users.html", {"users": User.objects.order_by("username")})


@staff_required
def user_create(request):
    form = AdminUserCreateForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"User {user.username} created.")
        return redirect("manage-users")
    return render(request, "reservations/manage/user_form.html", {"form": form, "heading": "Add user"})


@staff_required
def user_edit(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    form = AdminUserUpdateForm(request.POST or None, request.FILES or None, instance=user)
    if request.method == "POST" and form.is_valid():
        if user == request.user and (not form.cleaned_data["is_active"] or not form.cleaned_data["is_staff"]):
            form.add_error(None, "You cannot deactivate or remove administrator access from your own account.")
        else:
            form.save()
            messages.success(request, f"User {user.username} updated.")
            return redirect("manage-users")
    return render(request, "reservations/manage/user_form.html", {
        "form": form, "heading": f"Edit {user.username}", "edited_user": user
    })


@staff_required
def user_password(request, user_id):
    user = get_object_or_404(User, pk=user_id)
    form = SetPasswordForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Password reset for {user.username}.")
        return redirect("manage-users")
    return render(request, "reservations/manage/user_form.html", {
        "form": form, "heading": f"Reset password for {user.username}"
    })


@staff_required
def manage_systems(request):
    return render(request, "reservations/manage/systems.html", {"systems": System.objects.all()})


@staff_required
def system_create(request):
    form = SystemForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        system = form.save()
        messages.success(request, f"System {system.name} created.")
        return redirect("manage-systems")
    return render(request, "reservations/manage/system_form.html", {"form": form, "heading": "Add system"})


@staff_required
def system_edit(request, system_id):
    system = get_object_or_404(System, pk=system_id)
    form = SystemForm(request.POST or None, instance=system)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"System {system.name} updated.")
        return redirect("manage-systems")
    return render(request, "reservations/manage/system_form.html", {"form": form, "heading": f"Edit {system.name}"})


@require_GET
def health(request):
    try:
        User.objects.only("pk").first()
    except DatabaseError:
        return JsonResponse({"status": "unhealthy"}, status=503)
    return JsonResponse({"status": "ok"})
