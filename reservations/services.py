from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Reservation, System


class ReservationConflict(ValidationError):
    pass


MAX_SERIES_OCCURRENCES = 366


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


def _next_month(value, anchor_day):
    month_index = value.year * 12 + value.month
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(anchor_day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _occurrences(start, end, recurrence, recurrence_until):
    if recurrence not in Reservation.Recurrence.values:
        raise ValidationError("Invalid repeat frequency.")
    if recurrence == Reservation.Recurrence.NONE:
        return [(start, end)]
    if not recurrence_until:
        raise ValidationError("Choose an end date for the repeating reservation.")
    if recurrence_until < timezone.localdate(start):
        raise ValidationError("Repeat-until date cannot be before the first reservation.")

    duration = end - start
    anchor_day = timezone.localtime(start).day
    result = []
    current = start
    while timezone.localdate(current) <= recurrence_until:
        result.append((current, current + duration))
        if len(result) > MAX_SERIES_OCCURRENCES:
            raise ValidationError(f"A repeating reservation is limited to {MAX_SERIES_OCCURRENCES} occurrences.")
        if recurrence == Reservation.Recurrence.DAILY:
            following = current + timedelta(days=1)
        elif recurrence == Reservation.Recurrence.WEEKLY:
            following = current + timedelta(weeks=1)
        else:
            following = _next_month(current, anchor_day)
        if timezone.localdate(following) <= recurrence_until and following < current + duration:
            raise ValidationError("Each repeated reservation must end before the next one begins.")
        current = following
    return result


def create_reservations(*, actor, system_id, owner, start, end, purpose="", notes="", recurrence="none", recurrence_until=None):
    _validate_times(start, end, allow_past=actor.is_staff)
    occurrences = _occurrences(start, end, recurrence, recurrence_until)
    with transaction.atomic():
        system = _lock_systems([int(system_id)])[int(system_id)]
        if not system.is_active:
            raise ValidationError("This system is inactive.")

        existing = list(Reservation.objects.filter(
            system_id=system.pk,
            status=Reservation.Status.ACTIVE,
            start__lt=occurrences[-1][1],
            end__gt=occurrences[0][0],
        ).only("start", "end"))
        for occurrence_start, occurrence_end in occurrences:
            if any(item.start < occurrence_end and item.end > occurrence_start for item in existing):
                conflict_date = timezone.localtime(occurrence_start).strftime("%Y-%m-%d %H:%M")
                raise ReservationConflict(f"This system is already reserved at {conflict_date}.")

        series_id = uuid.uuid4() if recurrence != Reservation.Recurrence.NONE else None
        records = [Reservation(
            system=system,
            owner=owner,
            start=occurrence_start,
            end=occurrence_end,
            purpose=purpose.strip()[:160],
            notes=notes.strip(),
            series_id=series_id,
            recurrence=recurrence,
            recurrence_until=recurrence_until if series_id else None,
        ) for occurrence_start, occurrence_end in occurrences]
        Reservation.objects.bulk_create(records)
        return records


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


def cancel_reservation(*, actor, reservation_id, scope="single"):
    with transaction.atomic():
        reservation = Reservation.objects.select_for_update().select_related("owner").get(pk=reservation_id)
        _lock_systems([reservation.system_id])
        if not actor.is_staff and reservation.owner_id != actor.pk:
            raise PermissionError("You may cancel only your own reservations.")
        if reservation.status != Reservation.Status.ACTIVE:
            raise ValidationError("This reservation is already cancelled.")
        if not actor.is_staff and reservation.start <= timezone.now():
            raise PermissionError("Only future reservations can be cancelled.")
        now = timezone.now()
        if scope == "future" and reservation.series_id:
            future = Reservation.objects.filter(
                series_id=reservation.series_id,
                status=Reservation.Status.ACTIVE,
                start__gte=reservation.start,
            )
            if not actor.is_staff:
                future = future.filter(owner=actor)
            future.update(status=Reservation.Status.CANCELLED, cancelled_at=now, cancelled_by=actor, updated_at=now)
        else:
            reservation.status = Reservation.Status.CANCELLED
            reservation.cancelled_at = now
            reservation.cancelled_by = actor
            reservation.save(update_fields=["status", "cancelled_at", "cancelled_by", "updated_at"])
        return reservation
import calendar
import uuid
from datetime import timedelta
