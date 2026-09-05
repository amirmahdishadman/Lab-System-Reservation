from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Reservation, System


class ReservationConflict(ValidationError):
    pass


def _validate_times(start, end, *, allow_past=False):
    if not start or not end:
        raise ValidationError("Start and end times are required.")
    if end <= start:
        raise ValidationError("End time must be after start time.")
    if not allow_past and start < timezone.now():
        raise ValidationError("Reservations cannot start in the past.")


def _lock_systems(system_ids):
    systems = {
        system.pk: system
        for system in System.objects.select_for_update().filter(pk__in=sorted(set(system_ids))).order_by("pk")
    }
    if len(systems) != len(set(system_ids)):
        raise ValidationError("Selected system does not exist.")
    return systems


def save_reservation(*, actor, system_id, owner, start, end, purpose="", notes="", reservation_id=None):
    with transaction.atomic():
        reservation = None
        old_system_id = None
        if reservation_id is not None:
            reservation = Reservation.objects.select_for_update().select_related("owner").get(pk=reservation_id)
            old_system_id = reservation.system_id
            if not actor.is_staff and reservation.owner_id != actor.pk:
                raise PermissionError("You may edit only your own reservations.")
            if not actor.is_staff and (reservation.status != Reservation.Status.ACTIVE or reservation.start <= timezone.now()):
                raise PermissionError("Only future active reservations can be edited.")

        system_ids = [int(system_id)]
        if old_system_id:
            system_ids.append(old_system_id)
        systems = _lock_systems(system_ids)
        system = systems[int(system_id)]
        if not system.is_active:
            raise ValidationError("This system is inactive.")

        _validate_times(start, end, allow_past=actor.is_staff)
        conflicts = Reservation.objects.filter(
            system_id=system.pk,
            status=Reservation.Status.ACTIVE,
            start__lt=end,
            end__gt=start,
        )
        if reservation:
            conflicts = conflicts.exclude(pk=reservation.pk)
        if conflicts.exists():
            raise ReservationConflict("This system is already reserved during that time.")

        if reservation is None:
            reservation = Reservation()
        reservation.owner = owner
        reservation.system = system
        reservation.start = start
        reservation.end = end
        reservation.purpose = purpose.strip()[:160]
        reservation.notes = notes.strip()
        reservation.full_clean(exclude=["cancelled_by"])
        reservation.save()
        return reservation


def cancel_reservation(*, actor, reservation_id):
    with transaction.atomic():
        reservation = Reservation.objects.select_for_update().select_related("owner").get(pk=reservation_id)
        _lock_systems([reservation.system_id])
        if not actor.is_staff and reservation.owner_id != actor.pk:
            raise PermissionError("You may cancel only your own reservations.")
        if reservation.status != Reservation.Status.ACTIVE:
            raise ValidationError("This reservation is already cancelled.")
        if not actor.is_staff and reservation.start <= timezone.now():
            raise PermissionError("Only future reservations can be cancelled.")
        reservation.status = Reservation.Status.CANCELLED
        reservation.cancelled_at = timezone.now()
        reservation.cancelled_by = actor
        reservation.save(update_fields=["status", "cancelled_at", "cancelled_by", "updated_at"])
        return reservation
