from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.calendar_view, name="home"),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("calendar/", views.calendar_view, name="calendar"),
    path("notes/", views.notes_view, name="notes"),
    path("notes/mine/", views.update_note, name="update-note"),
    path("profile/", views.profile_view, name="profile"),
    path("media/avatar/<int:user_id>/", views.user_avatar, name="user-avatar"),
    path("api/reservations/", views.reservation_collection, name="reservation-collection"),
    path("api/reservations/<int:reservation_id>/", views.reservation_detail, name="reservation-detail"),
    path("manage/users/", views.manage_users, name="manage-users"),
    path("manage/users/add/", views.user_create, name="user-create"),
    path("manage/users/<int:user_id>/", views.user_edit, name="user-edit"),
    path("manage/users/<int:user_id>/password/", views.user_password, name="user-password"),
    path("manage/systems/", views.manage_systems, name="manage-systems"),
    path("manage/systems/add/", views.system_create, name="system-create"),
    path("manage/systems/<int:system_id>/", views.system_edit, name="system-edit"),
    path("health/", views.health, name="health"),
]
