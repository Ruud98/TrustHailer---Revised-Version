from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("join/", views.join, name="join"),
    path("join/verify/", views.verify, name="verify"),
    path("join/resend/", views.resend, name="resend"),
    path("login/", views.join, name="login"),
    path("logout/", views.logout, name="logout"),

    path("onboarding/role/", views.onboarding_role, name="onboarding_role"),
    path("onboarding/location/", views.onboarding_location, name="onboarding_location"),
    path("onboarding/profile/", views.onboarding_details, name="onboarding_details"),

    path("me/", views.me, name="me"),
    path("me/edit/", views.edit_profile, name="edit_profile"),
    path("me/settings/", views.account_settings, name="settings"),
    path("me/verify-phone/", views.verify_phone, name="verify_phone"),
    path("me/verify-phone/code/", views.verify_phone_code, name="verify_phone_code"),

    path("u/<slug:handle>/", views.profile, name="profile"),
]
