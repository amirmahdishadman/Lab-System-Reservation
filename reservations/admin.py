from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Reservation, System, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Profile", {"fields": ("display_name", "avatar")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Profile", {"fields": ("display_name", "avatar")}),)
    list_display = ("username", "display_name", "is_staff", "is_active")


@admin.register(System)
class SystemAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "color")
    list_filter = ("is_active",)


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ("system", "owner", "start", "end", "status")
    list_filter = ("status", "system")
    search_fields = ("owner__username", "owner__display_name", "purpose")
