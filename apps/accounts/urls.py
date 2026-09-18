from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("join/", views.join, name="join"),
    path("join/verify/", views.verify, name="verify"),
    path("join/resend/", views.resend, name="resend"),
    path("login/", views.login, name="login"),
    path("logout/", views.logout, name="logout"),

    path("onboarding/", views.onboarding, name="onboarding"),

    path("me/", views.me, name="me"),
    path("me/edit/", views.edit_profile, name="edit_profile"),
    path("me/settings/", views.account_settings, name="settings"),
    path("me/verification/", views.verification, name="verification"),

    # Staff only. The single path by which a stored identity document can be
    # read, so that every read is checked by Django and written to the log.
    path("staff/kyc/<int:pk>/file/", views.kyc_document, name="kyc_document"),

    path("u/<slug:handle>/", views.profile, name="profile"),
]
