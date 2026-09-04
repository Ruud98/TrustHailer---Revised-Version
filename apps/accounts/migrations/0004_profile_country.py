"""
Country becomes something we store rather than something we work out.

It used to be derivable: a profile had a suburb, the suburb had a city, the
city had a province, and the province had a country. Free-text cities end
that — somebody typing a city we have never seen gives us nothing to derive
from, and we need the country BEFORE we can file the new city under it.

So the value is backfilled here from the chain it used to be read off, and
asked for directly from here on. Profiles with no suburb fall back to what
their phone number says, then to ZA, which is what signup defaulted to anyway.
"""
from django.db import migrations, models


def backfill_country(apps, schema_editor):
    Profile = apps.get_model("accounts", "Profile")

    for profile in (
        Profile.objects.filter(suburb__isnull=False)
        .select_related("suburb__city__province")
        .iterator()
    ):
        country = profile.suburb.city.province.country
        if country and country != profile.country:
            profile.country = country
            profile.save(update_fields=["country"])

    # No suburb to read: the dialling code is the only other thing we hold.
    # Imported here rather than at module scope so a historical migration does
    # not depend on the import graph of the app as it stands today.
    from apps.core import phone as phone_utils

    for profile in (
        Profile.objects.filter(suburb__isnull=True).select_related("user").iterator()
    ):
        country = phone_utils.country_of(profile.user.phone)
        if country and country != profile.country:
            profile.country = country
            profile.save(update_fields=["country"])


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_profile_rating_avg_profile_rating_count'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='country',
            field=models.CharField(choices=[('ZA', 'South Africa'), ('ZW', 'Zimbabwe')], default='ZA', help_text='The market this member works in. Chosen at onboarding.', max_length=2),
        ),
        migrations.RunPython(backfill_country, migrations.RunPython.noop),
    ]
