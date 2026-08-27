from django.contrib.auth.backends import ModelBackend

from .models import User


class EmailBackend(ModelBackend):
    """
    Used by the OTP flow via `login(request, user, backend=...)`.

    Also supports password auth by email so `createsuperuser` accounts work at
    the admin login page — a separate door from the member OTP flow.
    """

    def authenticate(self, request, email=None, username=None, password=None, **kwargs):
        address = (email or username or "").strip().lower()
        if not address:
            return None
        try:
            user = User.objects.get(email__iexact=address)
        except User.DoesNotExist:
            # Equalise timing so a missing account isn't distinguishable.
            User().set_password(password or "")
            return None
        if password and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
