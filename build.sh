#!/usr/bin/env bash
#
# Build step for a container host. Render runs this; anything else that gives
# you a build hook can run the same four commands.
#
# WHY MIGRATE AND SEED HAPPEN HERE AND NOT BY HAND
# Render's free tier has no shell. There is no `manage.py migrate` you can go
# and type, so anything the database needs in order to serve a request has to
# happen in the build, or it never happens at all and the first visitor gets a
# 500 about a missing table.
#
# Both seed commands are idempotent — every row goes through get_or_create —
# so running them on each deploy costs a handful of SELECTs and changes
# nothing. That is the property that makes this safe; check it still holds
# before adding a third command to the list.
set -o errexit
set -o nounset
set -o pipefail

pip install --upgrade pip
pip install -r requirements.txt

# Hashes every filename and writes the gzip and brotli copies WhiteNoise serves.
# Must come before the app starts, and must not be skipped: a missing file here
# is a missing file in the service worker's precache list, and one of those
# means no service worker at all.
python manage.py collectstatic --no-input

python manage.py migrate --no-input

# 174 suburbs across Gauteng, Harare and Bulawayo, and the e-hailing platforms.
# An empty geo table is not a degraded site, it is an onboarding wizard with
# nothing to pick from.
python manage.py seed_geo --launch-market johannesburg --launch-market ekurhuleni
python manage.py seed_platforms
