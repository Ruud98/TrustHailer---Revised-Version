import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.ratelimit import RateLimited, client_ip, cooldown, hit

from .forms import (
    JoinForm,
    LocationForm,
    OTPForm,
    ProfileDetailsForm,
    RoleForm,
    SettingsForm,
)
from .models import OTPChallenge, User
from .notify import send_code

logger = logging.getLogger(__name__)

PENDING_EMAIL_KEY = "otp_email"
PENDING_CHALLENGE_KEY = "otp_id"
PENDING_PHONE_CHALLENGE_KEY = "phone_otp_id"

AUTH_BACKEND = "apps.accounts.backends.EmailBackend"


# ---------------------------------------------------------------- join / login

@require_http_methods(["GET", "POST"])
def join(request):
    """
    Enter an email address. One door for signup and login — there is no separate
    'register' path, which removes a whole class of user confusion, and no
    password, which removes a whole class of support tickets.
    """
    if request.user.is_authenticated:
        return redirect("home")

    form = JoinForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        ip = client_ip(request)
        try:
            _guard_send(email, ip)
        except RateLimited as exc:
            messages.error(request, exc.message)
            return render(request, "accounts/join.html", {"form": form})

        challenge, raw_code = OTPChallenge.issue(email, ip=ip)
        if not send_code(challenge, raw_code):
            messages.error(
                request,
                "We couldn't send that email just now. Please try again in a moment.",
            )
            return render(request, "accounts/join.html", {"form": form})

        request.session[PENDING_EMAIL_KEY] = email
        request.session[PENDING_CHALLENGE_KEY] = challenge.pk
        return redirect("accounts:verify")

    return render(request, "accounts/join.html", {"form": form})


def _guard_send(destination: str, ip: str) -> None:
    """
    Three limits, each closing a different hole.

    Email is free to send, so these are not a budget control the way they'd be
    for SMS. They protect two other things: the recipient, from having their
    inbox used as a weapon, and our sending reputation, which is the actual
    scarce resource. A domain that blasts unwanted mail gets filtered, and once
    Gmail decides you're spam every code you send afterwards disappears.
    """
    cooldown(
        f"otp:{destination}",
        settings.OTP_RESEND_COOLDOWN_SECONDS,
        "Please wait a moment before requesting another code.",
    )
    hit(
        f"otp:dest:{destination}",
        settings.OTP_MAX_PER_ADDRESS_PER_DAY,
        86400,
        "Too many codes requested for this address today. Try again tomorrow.",
    )
    hit(
        f"otp:ip:{ip}",
        settings.OTP_MAX_PER_IP_PER_HOUR,
        3600,
        "Too many requests from this connection. Please try again later.",
    )


@require_http_methods(["GET", "POST"])
def verify(request):
    """Enter the code. On success: log in, creating the user if new."""
    email = request.session.get(PENDING_EMAIL_KEY)
    challenge_id = request.session.get(PENDING_CHALLENGE_KEY)
    if not email or not challenge_id:
        messages.info(request, "Let's start again — enter your email address.")
        return redirect("accounts:join")

    form = OTPForm(request.POST or None)
    context = {
        "form": form,
        "destination": email,
        "resend_seconds": settings.OTP_RESEND_COOLDOWN_SECONDS,
    }

    if request.method == "POST" and form.is_valid():
        try:
            hit(f"otpcheck:{client_ip(request)}", 30, 3600,
                "Too many attempts. Please try again later.")
        except RateLimited as exc:
            messages.error(request, exc.message)
            return render(request, "accounts/verify.html", context)

        challenge = OTPChallenge.objects.filter(pk=challenge_id).first()
        if challenge is None or challenge.destination != email:
            messages.error(request, "That code has expired. Please request a new one.")
            return redirect("accounts:join")

        if not challenge.is_live:
            reason = (
                "Too many incorrect attempts."
                if challenge.attempts >= settings.OTP_MAX_ATTEMPTS
                else "That code has expired."
            )
            messages.error(request, f"{reason} Please request a new one.")
            return redirect("accounts:join")

        if challenge.verify(form.cleaned_data["code"]):
            user = _login_or_create(request, email)
            request.session.pop(PENDING_EMAIL_KEY, None)
            request.session.pop(PENDING_CHALLENGE_KEY, None)
            if not user.profile.is_onboarded:
                return redirect("accounts:onboarding_role")
            messages.success(request, f"Welcome back, {user.get_short_name()}.")
            return redirect("home")

        remaining = max(0, settings.OTP_MAX_ATTEMPTS - challenge.attempts)
        if remaining:
            messages.error(request, f"That code isn't right. {remaining} attempts left.")
        else:
            messages.error(request, "Too many incorrect attempts. Please request a new code.")
            return redirect("accounts:join")

    return render(request, "accounts/verify.html", context)


@transaction.atomic
def _login_or_create(request, email: str) -> User:
    user = User.objects.filter(email__iexact=email).first()
    created = False
    if user is None:
        user = User.objects.create_user(email=email, full_name="")
        created = True

    verification = user.verification
    if not verification.email_verified_at:
        verification.email_verified_at = timezone.now()
        verification.save(update_fields=["email_verified_at", "updated_at"])

    auth_login(request, user, backend=AUTH_BACKEND)
    logger.info("Login %s for %s", "created" if created else "existing", user.pk)
    return user


@require_POST
def resend(request):
    email = request.session.get(PENDING_EMAIL_KEY)
    if not email:
        return redirect("accounts:join")
    try:
        _guard_send(email, client_ip(request))
    except RateLimited as exc:
        messages.error(request, exc.message)
        return redirect("accounts:verify")

    challenge, raw_code = OTPChallenge.issue(email, ip=client_ip(request))
    if send_code(challenge, raw_code):
        request.session[PENDING_CHALLENGE_KEY] = challenge.pk
        messages.success(request, "New code sent.")
    else:
        messages.error(request, "We couldn't send that email. Please try again shortly.")
    return redirect("accounts:verify")


@require_POST
def logout(request):
    auth_logout(request)
    messages.success(request, "You've been logged out.")
    return redirect("home")


# --------------------------------------------------------- phone verification
# Deferred on purpose. Nothing here runs during signup.

@login_required
@require_http_methods(["GET", "POST"])
def verify_phone(request):
    """
    Verify the number already on file.

    Two channels, chosen by PHONE_VERIFICATION_CHANNEL:

    · "manual"  — no SMS, no cost. The user is asked to WhatsApp a code to the
                  support number and staff confirm it from the admin queue. At
                  seed scale, when you're hand-onboarding owners anyway, this is
                  both free and a better signal than an automated SMS.
    · "sms"     — the usual automated flow, once the manual queue outgrows you.
    """
    user = request.user
    if not user.phone:
        messages.info(request, "Add your mobile number first.")
        return redirect("accounts:edit_profile")

    if user.verification.phone_verified_at:
        messages.info(request, "Your number is already verified.")
        return redirect("accounts:me")

    channel = getattr(settings, "PHONE_VERIFICATION_CHANNEL", "manual")

    if channel == "manual":
        return render(
            request,
            "accounts/verify_phone_manual.html",
            {"support_whatsapp": settings.SUPPORT_WHATSAPP},
        )

    if request.method == "POST":
        try:
            _guard_send(user.phone, client_ip(request))
        except RateLimited as exc:
            messages.error(request, exc.message)
            return redirect("accounts:verify_phone")

        challenge, raw_code = OTPChallenge.issue(
            user.phone,
            channel=OTPChallenge.Channel.SMS,
            purpose=OTPChallenge.Purpose.PHONE,
            user=user,
            ip=client_ip(request),
        )
        if send_code(challenge, raw_code):
            request.session[PENDING_PHONE_CHALLENGE_KEY] = challenge.pk
            return redirect("accounts:verify_phone_code")
        messages.error(request, "We couldn't send that SMS. Please try again shortly.")

    return render(request, "accounts/verify_phone.html", {"phone": user.display_phone})


@login_required
@require_http_methods(["GET", "POST"])
def verify_phone_code(request):
    challenge_id = request.session.get(PENDING_PHONE_CHALLENGE_KEY)
    if not challenge_id:
        return redirect("accounts:verify_phone")

    form = OTPForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        challenge = OTPChallenge.objects.filter(
            pk=challenge_id, user=request.user, purpose=OTPChallenge.Purpose.PHONE
        ).first()
        if challenge is None or not challenge.is_live:
            messages.error(request, "That code has expired. Please request a new one.")
            return redirect("accounts:verify_phone")

        if challenge.verify(form.cleaned_data["code"]):
            verification = request.user.verification
            verification.phone_verified_at = timezone.now()
            verification.save(update_fields=["phone_verified_at", "updated_at"])
            request.session.pop(PENDING_PHONE_CHALLENGE_KEY, None)
            messages.success(request, "Your number is verified.")
            return redirect("accounts:me")

        messages.error(request, "That code isn't right.")

    return render(
        request,
        "accounts/verify.html",
        {
            "form": form,
            "destination": request.user.display_phone,
            "resend_seconds": settings.OTP_RESEND_COOLDOWN_SECONDS,
            "is_phone": True,
        },
    )


# ----------------------------------------------------------------- onboarding

@login_required
@require_http_methods(["GET", "POST"])
def onboarding_role(request):
    form = RoleForm(request.POST or None, instance=request.user.profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("accounts:onboarding_location")
    return render(request, "accounts/onboarding_role.html", {"form": form, "step": 1})


@login_required
@require_http_methods(["GET", "POST"])
def onboarding_location(request):
    form = LocationForm(request.POST or None, instance=request.user.profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("accounts:onboarding_details")
    return render(request, "accounts/onboarding_location.html", {"form": form, "step": 2})


@login_required
@require_http_methods(["GET", "POST"])
def onboarding_details(request):
    profile = request.user.profile
    form = ProfileDetailsForm(request.POST or None, request.FILES or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        profile = form.save()
        profile.onboarding_completed_at = timezone.now()
        profile.save(update_fields=["onboarding_completed_at", "updated_at"])
        messages.success(request, "You're all set. Welcome aboard.")
        return redirect("home")
    return render(request, "accounts/onboarding_details.html", {"form": form, "step": 3})


# -------------------------------------------------------------------- profile

def profile(request, handle):
    user = get_object_or_404(
        User.objects.select_related("profile__suburb__city", "verification"),
        handle=handle,
        is_active=True,
    )
    if user.profile.hide_from_search and request.user != user and not request.user.is_staff:
        raise Http404
    return render(
        request,
        "accounts/profile.html",
        {"profile_user": user, "profile": user.profile, "verification": user.verification},
    )


@login_required
def me(request):
    return redirect("accounts:profile", handle=request.user.handle)


@login_required
@require_http_methods(["GET", "POST"])
def edit_profile(request):
    form = ProfileDetailsForm(
        request.POST or None, request.FILES or None, instance=request.user.profile
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile updated.")
        return redirect("accounts:me")
    return render(request, "accounts/edit_profile.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def account_settings(request):
    form = SettingsForm(request.POST or None, instance=request.user.profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Settings saved.")
        return redirect("accounts:settings")
    return render(request, "accounts/settings.html", {"form": form})
