import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from io import BytesIO
from unittest import skipUnless

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from reservations.models import Reservation, System, User, UserNote
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

    def test_users_can_share_notes_and_only_update_their_own(self):
        response = self.client.post(reverse("update-note"), {"content": "  Microscope needs calibration.  "})
        self.assertRedirects(response, reverse("notes"))
        self.assertEqual(UserNote.objects.get(user=self.user).content, "Microscope needs calibration.")

        other_client = Client()
        other_client.force_login(self.other)
        page = other_client.get(reverse("notes"))
        self.assertContains(page, "Microscope needs calibration.")
        self.assertContains(page, "Alice")
        self.assertNotContains(other_client.get(reverse("calendar")), "Microscope needs calibration.")

        other_client.post(reverse("update-note"), {"content": "Bob's note"})
        self.assertEqual(UserNote.objects.get(user=self.user).content, "Microscope needs calibration.")
        self.assertEqual(UserNote.objects.get(user=self.other).content, "Bob's note")

    def test_note_can_be_cleared_and_anonymous_user_cannot_update_it(self):
        UserNote.objects.create(user=self.user, content="Temporary note")
        self.client.post(reverse("update-note"), {"content": "   "})
        self.assertEqual(UserNote.objects.get(user=self.user).content, "")
        self.assertNotContains(self.client.get(reverse("notes")), "Temporary note")

        anonymous = Client()
        self.assertEqual(anonymous.get(reverse("notes")).status_code, 302)
        self.assertEqual(anonymous.post(reverse("update-note"), {"content": "Unauthorized"}).status_code, 302)
        self.assertFalse(UserNote.objects.filter(content="Unauthorized").exists())

    def test_profile_image_can_be_uploaded_and_displayed(self):
        image_bytes = BytesIO()
        Image.new("RGBA", (32, 24), (25, 90, 180, 160)).save(image_bytes, format="PNG")
        upload = SimpleUploadedFile("avatar.png", image_bytes.getvalue(), content_type="image/png")

        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            response = self.client.post(reverse("profile"), {
                "action": "profile",
                "display_name": "Alice Updated",
                "avatar": upload,
            })
            self.assertRedirects(response, reverse("profile"))
            self.user.refresh_from_db()
            self.assertTrue(self.user.avatar.name.endswith(".webp"))
            avatar_response = self.client.get(reverse("user-avatar", args=[self.user.pk]))
            self.assertEqual(avatar_response.status_code, 200)
            self.assertEqual(avatar_response["Content-Type"], "image/webp")

    def test_invalid_profile_image_returns_a_form_error(self):
        upload = SimpleUploadedFile("broken.png", b"not an image", content_type="image/png")
        response = self.client.post(reverse("profile"), {
            "action": "profile",
            "display_name": "Alice",
            "avatar": upload,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "valid image")

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

    def test_daily_weekly_and_monthly_reservations(self):
        local_tz = timezone.get_current_timezone()
        next_year = timezone.localdate().year + 1
        cases = [
            ("daily", datetime(next_year, 1, 15, 9, tzinfo=local_tz), datetime(next_year, 1, 17, tzinfo=local_tz).date()),
            ("weekly", datetime(next_year, 1, 15, 9, tzinfo=local_tz), datetime(next_year, 1, 29, tzinfo=local_tz).date()),
            ("monthly", datetime(next_year, 1, 15, 9, tzinfo=local_tz), datetime(next_year, 3, 15, tzinfo=local_tz).date()),
        ]
        for index, (frequency, start, until) in enumerate(cases, start=1):
            system = System.objects.create(name=f"REPEAT-{index}")
            response = self.post_reservation(
                systemId=system.pk,
                start=start.strftime("%Y-%m-%dT%H:%M"),
                end=(start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
                recurrence=frequency,
                recurrenceUntil=until.isoformat(),
            )
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()["occurrenceCount"], 3)
            self.assertEqual(Reservation.objects.filter(system=system, recurrence=frequency).count(), 3)

    def test_recurring_series_is_atomic_when_one_occurrence_conflicts(self):
        start = timezone.localtime(timezone.now() + timedelta(days=10)).replace(second=0, microsecond=0)
        Reservation.objects.create(
            system=self.system,
            owner=self.other,
            start=start + timedelta(days=1),
            end=start + timedelta(days=1, hours=1),
        )
        response = self.post_reservation(
            start=start.strftime("%Y-%m-%dT%H:%M"),
            end=(start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            recurrence="daily",
            recurrenceUntil=(start + timedelta(days=2)).date().isoformat(),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Reservation.objects.filter(system=self.system).count(), 1)

    def test_cancel_this_and_following_keeps_earlier_occurrences(self):
        start = timezone.localtime(timezone.now() + timedelta(days=20)).replace(second=0, microsecond=0)
        response = self.post_reservation(
            start=start.strftime("%Y-%m-%dT%H:%M"),
            end=(start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            recurrence="daily",
            recurrenceUntil=(start + timedelta(days=2)).date().isoformat(),
        )
        self.assertEqual(response.status_code, 201)
        occurrences = list(Reservation.objects.filter(series_id__isnull=False).order_by("start"))
        url = reverse("reservation-detail", args=[occurrences[1].pk])
        cancelled = self.client.delete(url, json.dumps({"scope": "future"}), content_type="application/json")
        self.assertEqual(cancelled.status_code, 204)
        occurrences = list(Reservation.objects.filter(series_id=occurrences[0].series_id).order_by("start"))
        self.assertEqual([item.status for item in occurrences], ["active", "cancelled", "cancelled"])

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
