import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from apps.core.ratelimit import RateLimited, client_ip, cooldown, hit
from apps.listings.models import DriverListing, VehicleListing
from apps.safety.models import is_blocked_between

from .forms import (
    AccountType,
    LoginForm,
    SignupForm,
    OTPForm,
    OnboardingForm,
    ProfileDetailsForm,
    SettingsForm,
)
from .kyc_forms import VerificationDocumentForm
from .models import OTPChallenge, User, VerificationDocument
from .notify import send_code

logger = logging.getLogger(__name__)

PENDING_EMAIL_KEY = "otp_email"
PENDING_CHALLENGE_KEY = "otp_id"
# Everything a signup answered, held until the code comes back. Only ever
# carries a password HASH — see SignupForm.hashed_password.
PENDING_SIGNUP_KEY = "signup_details"

AUTH_BACKEND = "apps.accounts.backends.EmailBackend"


# ---------------------------------------------------------------- join / login

@require_http_methods(["GET", "POST"])
def join(request):
    """
    Sign up: name, email, password, and what you are here for.

    TWO DOORS NOW, NOT ONE
    ----------------------
    This used to be the only door — one email box that logged you in or made
    you an account depending on whether one existed. That is genuinely less
    confusing right up until signup has to ask for anything beyond an address,
    and asking a returning member for their name and what they are joining as
    is worse than asking a new one to find the login link.

    NOTHING IS CREATED HERE
    -----------------------
    The answers go into the session and the account appears in `verify`, when
    the code comes back. An address nobody can read still cannot hold an
    account, and a typo'd email leaves no dead row blocking the correct one.
    """
    if request.user.is_authenticated:
        return redirect("home")

    form = SignupForm(request.POST or None)
    # (value, label, blurb) per option. Built here rather than looked up in the
    # template, because Django cannot index a dict by a loop variable without a
    # custom filter and a filter for this would be machinery for one screen.
    blurbs = AccountType.blurbs()
    account_types = [
        (value, label, blurbs[AccountType(value)]) for value, label in AccountType.choices
    ]

    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        ip = client_ip(request)
        try:
            _guard_send(email, ip)
        except RateLimited as exc:
            messages.error(request, exc.message)
            return render(request, "accounts/join.html",
                      {"form": form, "account_types": account_types})

        challenge, raw_code = OTPChallenge.issue(email, ip=ip)
        if not send_code(challenge, raw_code):
            messages.error(
                request,
                "We couldn't send that email just now. Please try again in a moment.",
            )
            return render(request, "accounts/join.html",
                      {"form": form, "account_types": account_types})

        request.session[PENDING_EMAIL_KEY] = email
        request.session[PENDING_CHALLENGE_KEY] = challenge.pk
        request.session[PENDING_SIGNUP_KEY] = {
            "full_name": form.cleaned_data["full_name"],
            "password": form.hashed_password(),
            **form.profile_flags(),
        }
        return redirect("accounts:verify")

    return render(request, "accounts/join.html",
                      {"form": form, "account_types": account_types})


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
def login(request):
    """
    Email and password, or a code instead.

    THE CODE PATH IS NOT A COURTESY
    -------------------------------
    Members who joined before passwords existed have no usable one, and the
    only thing standing between them and a locked account is this button. It
    doubles as the forgotten-password journey, which is why there is no
    separate one: the answer to "I cannot remember it" is already on the page.

    Failures are deliberately vague. "That email and password do not match"
    says nothing about which half was wrong, so this form cannot be used to
    find out whether an address has an account — unlike signup, which has to
    reject duplicates and therefore cannot avoid saying so.
    """
    if request.user.is_authenticated:
        return redirect("home")

    form = LoginForm(request.POST or None)
    wants_code = "send_code" in request.POST

    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]

        if wants_code:
            return _start_code_login(request, email)

        password = form.cleaned_data.get("password")
        user = authenticate(request, email=email, password=password) if password else None
        if user is None:
            messages.error(request, "That email and password do not match.")
            return render(request, "accounts/login.html", {"form": form})
        if not user.can_participate:
            messages.error(request, "That account is suspended.")
            return render(request, "accounts/login.html", {"form": form})

        auth_login(request, user, backend=AUTH_BACKEND)
        logger.info("Password login for %s", user.pk)
        if not user.profile.is_onboarded:
            return redirect("accounts:onboarding")
        return redirect("home")

    return render(request, "accounts/login.html", {"form": form})


def _start_code_login(request, email):
    """Issue a code for an existing account and send them to the verify page."""
    ip = client_ip(request)
    try:
        _guard_send(email, ip)
    except RateLimited as exc:
        messages.error(request, exc.message)
        return redirect("accounts:login")

    # No signup details in the session, so `verify` will refuse to create an
    # account. An address with no account gets a code that cannot let anybody
    # in, which is the same answer a wrong password gets and tells an attacker
    # nothing either way.
    challenge, raw_code = OTPChallenge.issue(email, ip=ip)
    if not send_code(challenge, raw_code):
        messages.error(request, "We couldn't send that email just now. Try again shortly.")
        return redirect("accounts:login")

    request.session[PENDING_EMAIL_KEY] = email
    request.session[PENDING_CHALLENGE_KEY] = challenge.pk
    request.session.pop(PENDING_SIGNUP_KEY, None)
    return redirect("accounts:verify")


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
            details = request.session.get(PENDING_SIGNUP_KEY)
            user = _login_or_create(request, email, details)
            request.session.pop(PENDING_EMAIL_KEY, None)
            request.session.pop(PENDING_CHALLENGE_KEY, None)
            request.session.pop(PENDING_SIGNUP_KEY, None)
            if user is None:
                # A code for an address with no account. Said the same way a
                # wrong password is, so neither answer identifies an address.
                messages.error(request, "That email and code do not match.")
                return redirect("accounts:login")
            if not user.profile.is_onboarded:
                return redirect("accounts:onboarding")
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
def _login_or_create(request, email: str, details=None):
    """
    Log somebody in, creating the account only when a signup asked for one.

    `details` is what the signup form put in the session. Without it this is a
    code login, and a code login must never conjure an account: that was
    acceptable when the single email box was both doors, but now that signup
    collects a name, a password and an account type, an account made this way
    would have none of them.

    Returns None when there is nothing to log in to, and the caller says so in
    the same words a wrong password gets.
    """
    user = User.objects.filter(email__iexact=email).first()
    created = False
    if user is None:
        if not details:
            return None
        user = User.objects.create_user(
            email=email, full_name=details.get("full_name", "")
        )
        # Already hashed by the form — assigned, not set, so it is not hashed
        # twice.
        user.password = details["password"]
        user.save(update_fields=["password"])

        profile = user.profile
        profile.is_owner = details.get("is_owner", False)
        profile.is_driver = details.get("is_driver", False)
        profile.save(update_fields=["is_owner", "is_driver", "updated_at"])
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


# ----------------------------------------------------------------- onboarding

@login_required
@require_http_methods(["GET", "POST"])
def onboarding(request):
    """
    One screen, once. See `OnboardingForm` for what is asked and what is not.
    """
    profile = request.user.profile
    form = OnboardingForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        profile = form.save()
        profile.onboarding_completed_at = timezone.now()
        profile.save(update_fields=["onboarding_completed_at", "updated_at"])
        messages.success(request, "You're all set. Welcome aboard.")
        return redirect("home")
    return render(request, "accounts/onboarding.html", {"form": form})


# -------------------------------------------------------------------- profile

def profile(request, handle):
    user = get_object_or_404(
        User.objects.select_related("profile__suburb__city", "verification"),
        handle=handle,
        is_active=True,
    )
    if user.profile.hide_from_search and request.user != user and not request.user.is_staff:
        raise Http404

    # A blocked person's profile disappears for the blocker, and the blocker's
    # for them. Same 404 either way — nobody is told a block exists.
    if request.user != user and is_blocked_between(request.user, user):
        raise Http404

    is_self = request.user == user

    from apps.follows import services as follow_services

    follower_count, following_count = follow_services.counts(user)

    from apps.messaging import services as messaging_services

    can_message = messaging_services.can_message(request.user, user)

    # What this person has on the marketplace. Their own drafts and paused
    # listings show to them; everyone else sees only what is live.
    cars = VehicleListing.objects.filter(owner=user).with_display_data()
    driver_listing = DriverListing.objects.filter(driver=user).first()
    if not is_self and not request.user.is_staff:
        cars = cars.live()
        if driver_listing and not driver_listing.is_live:
            driver_listing = None

    return render(
        request,
        "accounts/profile.html",
        {
            "profile_user": user,
            "profile": user.profile,
            "verification": user.verification,
            "cars": cars.ranked(),
            "driver_listing": driver_listing,
            "is_self": is_self,
            "follower_count": follower_count,
            "following_count": following_count,
            "can_message": can_message,
        },
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


# ===========================================================================
#  Verification
# ===========================================================================


@login_required
@require_http_methods(["GET", "POST"])
def verification(request):
    """
    The ladder, and the one place a member sends us a document.

    WHAT THIS PAGE HAS TO SAY OUT LOUD
    ----------------------------------
    That the document is deleted as soon as somebody has looked at it. People
    are right to be wary of uploading an ID to a website they found through a
    Facebook group — that wariness is the correct instinct and we should not
    talk anybody out of it. Saying plainly what happens to the file, and then
    doing exactly that, is the only version of this that deserves the upload.
    """
    documents = request.user.kyc_documents.order_by("-created_at")
    form = VerificationDocumentForm(
        request.POST or None, request.FILES or None, user=request.user
    )

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(
            request,
            "Sent for checking. We delete the file as soon as we have looked at it.",
        )
        return redirect("accounts:verification")

    return render(
        request,
        "accounts/verification.html",
        {
            "form": form,
            "documents": documents,
            "verification": request.user.verification,
            "pending_kinds": {
                doc.kind
                for doc in documents
                if doc.status == VerificationDocument.Status.PENDING
            },
        },
    )


@staff_member_required
def kyc_document(request, pk):
    """
    Stream one identity document to a reviewer. The only way to read one.

    Nothing renders a URL to a stored document — not the admin, not this app —
    because a URL is a thing that leaks into browser history, referrer headers
    and a screenshot of a support ticket. Going through a view means Django
    checks staff status on every single read, and it means the read can be
    logged, which it is. Looking at somebody's ID is an event worth having a
    record of.
    """
    document = get_object_or_404(VerificationDocument, pk=pk)
    if not document.file:
        raise Http404("Deleted on review, as intended.")

    logger.info(
        "KYC document %s (%s, user %s) opened by staff %s",
        document.pk, document.kind, document.user_id, request.user.pk,
    )
    response = FileResponse(document.file.open("rb"))
    # Never let a proxy or a browser keep a copy of somebody's ID.
    response["Cache-Control"] = "no-store, max-age=0"
    response["Content-Disposition"] = f'inline; filename="document-{document.pk}"'
    return response
