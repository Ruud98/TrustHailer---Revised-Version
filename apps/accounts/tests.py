import io
import re
from datetime import timedelta

from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.core import phone as phone_utils
from apps.core import pricing
from apps.core.images import ImageProcessingError, process_upload
from apps.core.ratelimit import RateLimited, cooldown, hit
from apps.geo.models import City, Country, Province, Suburb

from .models import OTPChallenge, User

AUTH_BACKEND = "apps.accounts.backends.EmailBackend"


def code_from_last_email():
    """Pull the 6-digit code out of the most recent outbound email."""
    body = mail.outbox[-1].body
    match = re.search(r"\b(\d{6})\b", body)
    assert match, f"No code found in email body: {body!r}"
    return match.group(1)


class PhoneNormalisationTests(TestCase):
    def test_south_african_formats_all_normalise(self):
        for raw in ["0821234567", "082 123 4567", "082-123-4567",
                    "+27821234567", "+27 82 123 4567", "0027821234567"]:
            with self.subTest(raw=raw):
                self.assertEqual(phone_utils.normalise(raw), "+27821234567")

    def test_zimbabwe_numbers(self):
        self.assertEqual(phone_utils.normalise("0772123456", "ZW"), "+263772123456")

    def test_dial_code_263_is_not_swallowed_by_27(self):
        self.assertEqual(phone_utils.country_of("+263772123456"), "ZW")
        self.assertEqual(phone_utils.country_of("+27821234567"), "ZA")

    def test_landlines_are_rejected(self):
        with self.assertRaises(phone_utils.PhoneError):
            phone_utils.normalise("0111234567")

    def test_mask_does_not_leak_the_middle_digits(self):
        self.assertEqual(phone_utils.mask("+27821234567"), "082 *** 4567")
        self.assertNotIn("123", phone_utils.mask("+27821234567"))


class OTPChallengeTests(TestCase):
    def test_code_is_hashed_not_stored_raw(self):
        challenge, raw = OTPChallenge.issue("a@example.com")
        self.assertNotIn(raw, challenge.code_hash)
        self.assertEqual(len(raw), settings.OTP_LENGTH)

    def test_correct_code_verifies_once_only(self):
        challenge, raw = OTPChallenge.issue("a@example.com")
        self.assertTrue(challenge.verify(raw))
        self.assertFalse(challenge.verify(raw))

    def test_code_dies_after_max_attempts(self):
        challenge, raw = OTPChallenge.issue("a@example.com")
        wrong = "999999" if raw != "999999" else "111111"
        for _ in range(settings.OTP_MAX_ATTEMPTS):
            challenge.verify(wrong)
        self.assertFalse(challenge.is_live)
        self.assertFalse(challenge.verify(raw), "Brute-forced code must be dead even if guessed")

    def test_expired_code_does_not_verify(self):
        challenge, raw = OTPChallenge.issue("a@example.com")
        challenge.expires_at = timezone.now() - timedelta(seconds=1)
        challenge.save(update_fields=["expires_at"])
        self.assertFalse(challenge.verify(raw))

    def test_issuing_a_new_code_invalidates_the_previous_one(self):
        first, first_raw = OTPChallenge.issue("a@example.com")
        OTPChallenge.issue("a@example.com")
        first.refresh_from_db()
        self.assertFalse(first.verify(first_raw))

    def test_purposes_do_not_collide(self):
        """A live login code must survive another purpose being issued."""
        login, login_raw = OTPChallenge.issue("a@example.com")
        OTPChallenge.issue(
            "a@example.com", purpose=OTPChallenge.Purpose.EMAIL_CHANGE
        )
        login.refresh_from_db()
        self.assertTrue(login.is_live)
        self.assertTrue(login.verify(login_raw))


class JoinFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        province = Province.objects.create(country=Country.ZA, name="Gauteng", slug="za-gauteng")
        self.city = City.objects.create(
            province=province, name="Johannesburg", slug="johannesburg", is_launch_market=True
        )
        self.suburb = Suburb.objects.create(city=self.city, name="Soweto", slug="soweto")

    def test_join_sends_an_email_and_redirects_to_verify(self):
        response = self.client.post(reverse("accounts:join"), {"email": "Thabo@Example.com"})
        self.assertRedirects(response, reverse("accounts:verify"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["thabo@example.com"])

    def test_signup_issues_only_a_login_code(self):
        """
        Signup costs one email and nothing else. There is no second channel left
        to accidentally bill against — see 0005_drop_phone_verification.
        """
        self.client.post(reverse("accounts:join"), {"email": "thabo@example.com"})
        self.client.post(reverse("accounts:verify"), {"code": code_from_last_email()})
        self.assertEqual(
            list(OTPChallenge.objects.values_list("purpose", flat=True)),
            [OTPChallenge.Purpose.LOGIN],
        )

    def test_the_code_is_never_returned_in_the_response(self):
        self.client.post(reverse("accounts:join"), {"email": "thabo@example.com"})
        code = code_from_last_email()
        self.assertNotContains(self.client.get(reverse("accounts:verify")), code)

    def test_email_is_lowercased_so_case_does_not_split_accounts(self):
        self.client.post(reverse("accounts:join"), {"email": "Thabo@Example.com"})
        self.client.post(reverse("accounts:verify"), {"code": code_from_last_email()})
        self.client.post(reverse("accounts:logout"))
        cache.clear()
        self.client.post(reverse("accounts:join"), {"email": "THABO@example.com"})
        self.client.post(reverse("accounts:verify"), {"code": code_from_last_email()})
        self.assertEqual(User.objects.filter(email="thabo@example.com").count(), 1)

    def test_full_signup_creates_an_email_verified_user(self):
        self.client.post(reverse("accounts:join"), {"email": "thabo@example.com"})
        response = self.client.post(reverse("accounts:verify"), {"code": code_from_last_email()})

        user = User.objects.get(email="thabo@example.com")
        self.assertRedirects(response, reverse("accounts:onboarding_role"))
        self.assertIsNotNone(user.verification.email_verified_at)
        self.assertIsNone(user.phone, "No number is asked for until onboarding")
        self.assertFalse(user.has_usable_password())

    def test_wrong_code_does_not_create_an_account(self):
        self.client.post(reverse("accounts:join"), {"email": "thabo@example.com"})
        self.client.post(reverse("accounts:verify"), {"code": "000000"})
        self.assertFalse(User.objects.filter(email="thabo@example.com").exists())

    def test_resend_cooldown_blocks_an_immediate_second_email(self):
        self.client.post(reverse("accounts:join"), {"email": "thabo@example.com"})
        self.client.post(reverse("accounts:resend"))
        self.assertEqual(len(mail.outbox), 1, "Cooldown must stop the second email")

    def test_verify_without_a_pending_challenge_bounces_to_join(self):
        self.assertRedirects(self.client.get(reverse("accounts:verify")),
                             reverse("accounts:join"))


class OnboardingTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        province = Province.objects.create(country=Country.ZA, name="Gauteng", slug="za-gauteng")
        self.city = City.objects.create(
            province=province, name="Johannesburg", slug="johannesburg", is_launch_market=True
        )
        self.suburb = Suburb.objects.create(city=self.city, name="Soweto", slug="soweto")
        self.user = User.objects.create_user(email="thabo@example.com", full_name="")
        self.client.force_login(self.user, backend=AUTH_BACKEND)

    def _finish(self, phone="082 123 4567"):
        self.client.post(reverse("accounts:onboarding_role"), {"is_driver": "on"})
        self.client.post(reverse("accounts:onboarding_location"),
                         {"city": self.city.pk, "suburb": self.suburb.pk})
        return self.client.post(reverse("accounts:onboarding_details"),
                                {"full_name": "Thabo Mokoena", "country": "ZA",
                                 "phone": phone, "bio": "", "whatsapp_ok": "on"})

    def test_unfinished_onboarding_redirects_away_from_the_feed(self):
        self.assertRedirects(self.client.get("/"), reverse("accounts:onboarding_role"))

    def test_role_step_requires_at_least_one_role(self):
        self.assertEqual(self.client.post(reverse("accounts:onboarding_role"), {}).status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.is_owner)

    def test_owner_and_driver_can_both_be_selected(self):
        self.client.post(reverse("accounts:onboarding_role"),
                         {"is_owner": "on", "is_driver": "on"})
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.is_owner)
        self.assertTrue(self.user.profile.is_driver)

    def test_suburb_outside_a_launch_market_is_rejected(self):
        other_province = Province.objects.create(
            country=Country.ZA, name="Limpopo", slug="za-limpopo")
        other_city = City.objects.create(
            province=other_province, name="Polokwane", slug="polokwane", is_launch_market=False)
        elsewhere = Suburb.objects.create(city=other_city, name="Seshego", slug="seshego")

        response = self.client.post(reverse("accounts:onboarding_location"),
                                    {"city": other_city.pk, "suburb": elsewhere.pk})
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertIsNone(self.user.profile.suburb)

    def test_onboarding_collects_the_phone(self):
        """
        Still collected, never verified. The number is what gets released when
        an introduction is approved, so it has to be on file.
        """
        self._finish()
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, "+27821234567")

    def test_completing_all_three_steps_unlocks_the_app(self):
        self._finish()
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.is_onboarded)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_invalid_phone_blocks_the_final_step(self):
        response = self._finish(phone="0111234567")  # landline
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.is_onboarded)

    def test_phone_already_on_another_account_is_rejected(self):
        User.objects.create_user(email="other@example.com", full_name="Other").__class__ \
            .objects.filter(email="other@example.com").update(phone="+27821234567")
        response = self._finish(phone="082 123 4567")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.phone)

    def test_two_users_without_phones_do_not_collide_on_the_unique_index(self):
        """Empty phone must store as NULL, not "", or the second user fails."""
        User.objects.create_user(email="a@example.com", full_name="A")
        User.objects.create_user(email="b@example.com", full_name="B")
        self.assertEqual(User.objects.filter(phone__isnull=True).count(), 3)

    def test_the_number_can_be_changed(self):
        self._finish()
        self.user.refresh_from_db()
        self.client.post(reverse("accounts:edit_profile"),
                         {"full_name": "Thabo Mokoena", "country": "ZA",
                          "phone": "083 999 1111", "bio": "", "whatsapp_ok": "on"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, "+27839991111")


class VerificationLadderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="a@example.com", full_name="A B")

    def test_email_only_sits_at_the_bottom_of_the_ladder(self):
        v = self.user.verification
        v.email_verified_at = timezone.now()
        v.save()
        self.assertEqual(v.level, v.LEVEL_EMAIL)
        self.assertEqual(v.label, "Email verified")

    def test_id_outranks_email(self):
        v = self.user.verification
        v.email_verified_at = v.id_verified_at = timezone.now()
        v.save()
        self.assertEqual(v.level, v.LEVEL_ID)

    def test_expired_licence_drops_the_level(self):
        v = self.user.verification
        v.id_verified_at = v.licence_verified_at = timezone.now()
        v.licence_expires_on = timezone.localdate() - timedelta(days=1)
        v.save()
        self.assertFalse(v.licence_valid)
        self.assertEqual(v.level, v.LEVEL_ID)


class PricingTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(email="owner@example.com", full_name="Owner")
        self.owner.profile.is_owner = True
        self.owner.profile.save()

        self.driver = User.objects.create_user(email="driver@example.com", full_name="Driver")
        self.driver.profile.is_driver = True
        self.driver.profile.save()

    @override_settings(MONETISATION_ENABLED=False)
    def test_everything_is_free_at_launch(self):
        price = pricing.price_for(pricing.Action.INTRO_APPROVE, user=self.owner)
        self.assertEqual(price.credits, 0)
        self.assertTrue(price.is_free)
        self.assertFalse(price.is_chargeable)

    @override_settings(MONETISATION_ENABLED=True)
    def test_owners_are_charged_once_monetisation_is_on(self):
        price = pricing.price_for(pricing.Action.INTRO_APPROVE, user=self.owner)
        self.assertGreater(price.credits, 0)
        self.assertTrue(price.is_chargeable)

    @override_settings(MONETISATION_ENABLED=True)
    def test_drivers_are_never_charged_even_with_monetisation_on(self):
        """A product commitment, not a launch promotion. This test holds the line."""
        for action in [pricing.Action.INTRO_APPROVE, pricing.Action.LISTING_BOOST,
                       pricing.Action.VETTING_REPORT]:
            with self.subTest(action=action):
                price = pricing.price_for(action, user=self.driver)
                self.assertEqual(price.credits, 0)
                self.assertTrue(price.is_free)

    @override_settings(MONETISATION_ENABLED=True)
    def test_owner_drivers_are_treated_as_owners(self):
        self.driver.profile.is_owner = True
        self.driver.profile.save()
        price = pricing.price_for(pricing.Action.INTRO_APPROVE, user=self.driver)
        self.assertGreater(price.credits, 0)

    @override_settings(MONETISATION_ENABLED=False)
    def test_launch_notice_sets_expectations_honestly(self):
        notice = pricing.launch_notice()
        self.assertIn("free while we're building", notice.lower())
        self.assertNotIn("free forever", notice.lower())


class ProfilePrivacyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="thabo@example.com", full_name="Thabo Mokoena")
        User.objects.filter(pk=self.user.pk).update(phone="+27821234567")
        self.user.refresh_from_db()
        self.user.profile.onboarding_completed_at = timezone.now()
        self.user.profile.save()

    def test_profile_page_never_shows_the_raw_number(self):
        response = self.client.get(reverse("accounts:profile", args=[self.user.handle]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "0821234567")
        self.assertNotContains(response, "+27821234567")
        self.assertContains(response, "082 *** 4567")

    def test_profile_page_never_shows_the_email(self):
        response = self.client.get(reverse("accounts:profile", args=[self.user.handle]))
        self.assertNotContains(response, "thabo@example.com")

    def test_hidden_profiles_404_for_strangers(self):
        self.user.profile.hide_from_search = True
        self.user.profile.save()
        self.assertEqual(
            self.client.get(reverse("accounts:profile", args=[self.user.handle])).status_code, 404
        )


class HandleTests(TestCase):
    def test_signup_without_a_name_gets_a_placeholder_handle(self):
        user = User.objects.create_user(email="a@example.com", full_name="")
        self.assertTrue(user.has_placeholder_handle)

    def test_placeholder_is_upgraded_when_a_name_arrives(self):
        user = User.objects.create_user(email="a@example.com", full_name="")
        user.full_name = "Thabo Mokoena"
        self.assertTrue(user.refresh_handle_if_placeholder())
        self.assertEqual(user.handle, "thabo-mokoena")

    def test_an_established_handle_is_never_rewritten(self):
        user = User.objects.create_user(email="a@example.com", full_name="Thabo Mokoena")
        original = user.handle
        user.full_name = "Thabo M Mokoena"
        self.assertFalse(user.refresh_handle_if_placeholder())
        self.assertEqual(user.handle, original)

    def test_handles_are_unique_for_identical_names(self):
        first = User.objects.create_user(email="a@example.com", full_name="Thabo Mokoena")
        second = User.objects.create_user(email="b@example.com", full_name="Thabo Mokoena")
        self.assertNotEqual(first.handle, second.handle)


class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_hit_raises_once_over_the_limit(self):
        for _ in range(3):
            hit("k", 3, 60)
        with self.assertRaises(RateLimited):
            hit("k", 3, 60)

    def test_cooldown_blocks_the_second_call(self):
        cooldown("c", 60)
        with self.assertRaises(RateLimited):
            cooldown("c", 60)

    def test_limits_are_isolated_per_key(self):
        hit("a", 1, 60)
        hit("b", 1, 60)


class ImagePipelineTests(TestCase):
    @staticmethod
    def _png(width=2400, height=1600, colour=(200, 30, 30)):
        buffer = io.BytesIO()
        Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
        buffer.seek(0)
        buffer.size = buffer.getbuffer().nbytes
        buffer.name = "photo.png"
        return buffer

    def test_large_upload_is_downscaled_and_converted_to_webp(self):
        display, thumb = process_upload(self._png())
        with Image.open(io.BytesIO(display.read())) as img:
            self.assertEqual(img.format, "WEBP")
            self.assertLessEqual(max(img.size), settings.IMAGE_MAX_EDGE)
        with Image.open(io.BytesIO(thumb.read())) as img:
            self.assertLessEqual(max(img.size), settings.IMAGE_THUMB_EDGE)

    def test_small_images_are_not_upscaled(self):
        display, _ = process_upload(self._png(300, 200))
        with Image.open(io.BytesIO(display.read())) as img:
            self.assertEqual(img.size, (300, 200))

    def test_exif_is_stripped(self):
        """GPS in a photo of a car parked at home would publish someone's address."""
        source = Image.new("RGB", (800, 600), (10, 90, 200))
        exif = source.getexif()
        exif[0x010F] = "TestCameraMake"
        buffer = io.BytesIO()
        source.save(buffer, format="JPEG", exif=exif)
        buffer.seek(0)
        buffer.size = buffer.getbuffer().nbytes
        buffer.name = "with_exif.jpg"

        display, _ = process_upload(buffer)
        with Image.open(io.BytesIO(display.read())) as img:
            self.assertEqual(dict(img.getexif()), {})

    def test_non_image_is_rejected(self):
        buffer = io.BytesIO(b"this is definitely not an image")
        buffer.size = buffer.getbuffer().nbytes
        buffer.name = "payload.png"
        with self.assertRaises(ImageProcessingError):
            process_upload(buffer)

    def test_oversized_file_is_rejected_before_decoding(self):
        buffer = io.BytesIO(b"x")
        buffer.size = settings.IMAGE_MAX_UPLOAD_BYTES + 1
        buffer.name = "huge.jpg"
        with self.assertRaises(ImageProcessingError):
            process_upload(buffer)

    def test_transparency_is_flattened_onto_white(self):
        source = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        buffer = io.BytesIO()
        source.save(buffer, format="PNG")
        buffer.seek(0)
        buffer.size = buffer.getbuffer().nbytes
        buffer.name = "transparent.png"

        display, _ = process_upload(buffer)
        with Image.open(io.BytesIO(display.read())) as img:
            self.assertEqual(img.convert("RGB").getpixel((5, 5)), (255, 255, 255))
