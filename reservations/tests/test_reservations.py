import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest import skipUnless

from django.db import connection, connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from reservations.models import Reservation, System, User
from reservations.services import ReservationConflict, save_reservation


class ReservationApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", display_name="Alice", password="test-password-123")
        self.other = User.objects.create_user(username="bob", display_name="Bob", password="test-password-123")
        self.admin = User.objects.create_user(username="admin", display_name="Admin", password="test-password-123", is_staff=True)
        self.system = System.objects.create(name="LAB-01")
        self.client.force_login(self.user)
        local_start = timezone.localtime(timezone.now() + timedelta(days=2)).replace(second=0, microsecond=0)
        self.start = local_start.strftime("%Y-%m-%dT%H:%M")
        self.end = (local_start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")

    def post_reservation(self, **overrides):
        payload = {"systemId": self.system.pk, "start": self.start, "end": self.end, "purpose": "Experiment"}
        payload.update(overrides)
        return self.client.post(reverse("reservation-collection"), json.dumps(payload), content_type="application/json")

    def test_login_is_required_and_registration_does_not_exist(self):
        anonymous = Client()
        self.assertEqual(anonymous.get(reverse("calendar")).status_code, 302)
        self.assertEqual(anonymous.get("/register/").status_code, 404)

    def test_create_overlap_rejected_and_adjacent_allowed(self):
        self.assertEqual(self.post_reservation().status_code, 201)
        overlap_start = timezone.localtime(timezone.now() + timedelta(days=2, hours=1)).replace(second=0, microsecond=0)
        overlap = self.post_reservation(
            start=overlap_start.strftime("%Y-%m-%dT%H:%M"),
            end=(overlap_start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
        )
        self.assertEqual(overlap.status_code, 409)
        adjacent = self.post_reservation(
            start=self.end,
            end=(timezone.localtime(timezone.now() + timedelta(days=2, hours=3)).replace(second=0, microsecond=0)).strftime("%Y-%m-%dT%H:%M"),
        )
        self.assertEqual(adjacent.status_code, 201)

    def test_user_cannot_edit_or_cancel_another_users_reservation(self):
        reservation = Reservation.objects.create(
            system=self.system,
            owner=self.other,
            start=timezone.now() + timedelta(days=3),
            end=timezone.now() + timedelta(days=3, hours=1),
        )
        payload = {"systemId": self.system.pk, "start": self.start, "end": self.end}
        url = reverse("reservation-detail", args=[reservation.pk])
        self.assertEqual(self.client.patch(url, json.dumps(payload), content_type="application/json").status_code, 403)
        self.assertEqual(self.client.delete(url).status_code, 403)

    def test_admin_can_create_for_another_user(self):
        self.client.force_login(self.admin)
        response = self.post_reservation(ownerId=self.other.pk)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Reservation.objects.get().owner, self.other)

    def test_inactive_system_cannot_be_reserved(self):
        self.system.is_active = False
        self.system.save()
        self.assertEqual(self.post_reservation().status_code, 400)

    def test_cancelled_reservation_releases_slot(self):
        created = self.post_reservation().json()
        self.assertEqual(self.client.delete(reverse("reservation-detail", args=[created["id"]])).status_code, 204)
        self.assertEqual(self.post_reservation().status_code, 201)

    def test_management_pages_are_staff_only(self):
        self.assertEqual(self.client.get(reverse("manage-users")).status_code, 302)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("manage-users")).status_code, 200)
        self.assertEqual(self.client.get(reverse("manage-systems")).status_code, 200)


@skipUnless(connection.vendor == "mysql", "Concurrency locking test requires MariaDB/MySQL")
class MariaDbConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_user(username="concurrent", display_name="Concurrent", password="password-123")
        self.system = System.objects.create(name="LOCKED-01")
        self.start = timezone.now() + timedelta(days=5)
        self.end = self.start + timedelta(hours=1)

    def _book(self):
        connections.close_all()
        try:
            user = User.objects.get(pk=self.user.pk)
            save_reservation(actor=user, owner=user, system_id=self.system.pk, start=self.start, end=self.end)
            return "created"
        except ReservationConflict:
            return "conflict"
        finally:
            connections.close_all()

    def test_concurrent_overlapping_requests_only_create_one_booking(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self._book(), range(2)))
        self.assertCountEqual(results, ["created", "conflict"])
        self.assertEqual(Reservation.objects.filter(status=Reservation.Status.ACTIVE).count(), 1)
