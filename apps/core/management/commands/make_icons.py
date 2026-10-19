"""
Draw the app icons from the same geometry as `static/img/icon.svg`.

WHY THIS IS A COMMAND AND NOT A ONE-OFF SCRIPT SOMEBODY RAN ONCE
-----------------------------------------------------------------
The PNGs are committed — a manifest that points at a file built at deploy time
is a manifest that is broken on any box where the build step was skipped, and
the symptom is "the install button never appears", which nobody debugs for
weeks. But committed binaries drift from the vector they were traced off, so
the tracing lives here: change `icon.svg`, change the constants below to match,
re-run, commit the result. One source of truth with a reproducible path from it
to the files, rather than a PNG somebody exported from a design tool in 2024.

Pillow cannot read SVG, which is why the geometry is restated rather than
rasterised. The numbers below are the viewBox-64 coordinates from the file,
scaled by `size / 64`.

WHY THE MASKABLE ICON IS A SEPARATE FILE
-----------------------------------------
Android crops a launcher icon to whatever shape the launcher wants — circle,
squircle, teardrop — keeping only the middle 80%. Handing it the normal icon
means the rounded corners get cropped off and the tick touches the edge. So the
maskable variant draws the same mark at 80% scale on a full-bleed background:
whatever shape gets cut out of it, the tick survives whole.
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from PIL import Image, ImageDraw

# --------------------------------------------------- geometry, in viewBox units
# Straight off static/img/icon.svg. Keep these two in step.

VIEWBOX = 64
BACKGROUND = "#12161C"
CORNER_RADIUS = 14
TICK_COLOUR = "#E07A0C"
TICK_WIDTH = 6
TICK_POINTS = [(16, 33.5), (25.5, 43), (48, 21)]
ROAD_Y = 48
ROAD_FROM, ROAD_TO = 16, 48
ROAD_WIDTH = 3
ROAD_DASH = 6                      # stroke-dasharray "6 6": on 6, off 6
ROAD_ALPHA = round(255 * 0.65)     # opacity=".65"

# Drawn this many times larger and scaled down at the end. Pillow has no
# antialiasing on `line` or `ellipse`, so the only way to get a clean diagonal
# — and the tick is nothing but diagonals — is to supersample.
SUPERSAMPLE = 4

# The sizes the manifest and the iOS meta tag actually ask for. Nothing else:
# an icon size no document references is a file that only costs bandwidth.
TARGETS = [
    ("icon-192.png", 192, False),
    ("icon-512.png", 512, False),
    ("icon-maskable-512.png", 512, True),
    # iOS ignores the manifest's icons and reads `apple-touch-icon` instead.
    # 180 is the iPhone's own size, and it must be opaque with square corners —
    # iOS rounds it itself, and rounds a second time over anything we round.
    ("apple-touch-icon.png", 180, False),
]


class Command(BaseCommand):
    help = "Render the PWA icon set into static/img/ from icon.svg's geometry."

    def handle(self, *args, **options):
        out = Path(settings.BASE_DIR) / "static" / "img"
        out.mkdir(parents=True, exist_ok=True)

        for name, size, maskable in TARGETS:
            _render(size, maskable).save(out / name, "PNG", optimize=True)
            self.stdout.write(self.style.SUCCESS(f"wrote {name} ({size}px)"))


def _render(size, maskable):
    canvas = size * SUPERSAMPLE
    image = Image.new("RGB", (canvas, canvas), BACKGROUND)
    draw = ImageDraw.Draw(image)

    # The mark shrinks for the maskable variant; the background does not, so it
    # stays full-bleed and there is colour under whatever the launcher crops to.
    inset = canvas * 0.1 if maskable else 0
    scale = (canvas - inset * 2) / VIEWBOX

    def point(x, y):
        return (inset + x * scale, inset + y * scale)

    if not maskable:
        # Square corners on the maskable one: the launcher's mask is the only
        # rounding that should be visible, and rounding twice reads as a mistake.
        _rounded_corners(image, CORNER_RADIUS * scale)

    _stroke(draw, [point(*p) for p in TICK_POINTS], TICK_WIDTH * scale, TICK_COLOUR)

    # The dashed road goes on its own layer because it is translucent, and
    # drawing one translucent stroke over another where the dashes' round caps
    # overlap would darken it at the joins. Composited once, so it does not.
    road = Image.new("RGBA", image.size, (0, 0, 0, 0))
    road_draw = ImageDraw.Draw(road)
    white = (255, 255, 255, ROAD_ALPHA)
    x = ROAD_FROM
    while x < ROAD_TO:
        end = min(x + ROAD_DASH, ROAD_TO)
        _stroke(road_draw, [point(x, ROAD_Y), point(end, ROAD_Y)],
                ROAD_WIDTH * scale, white)
        x += ROAD_DASH * 2
    image = Image.alpha_composite(image.convert("RGBA"), road).convert("RGB")

    return image.resize((size, size), Image.LANCZOS)


def _stroke(draw, points, width, colour):
    """
    A polyline with round caps and round joins, which Pillow does not have.

    `joint="curve"` rounds the joins but leaves the two ends cut square, and
    the tick's geometry is `stroke-linecap="round"` — square ends make the
    short arm look clipped. So the line is drawn and then a disc is stamped at
    every vertex, which is what a round cap and a round join both are.
    """
    draw.line(points, fill=colour, width=max(1, round(width)), joint="curve")
    radius = width / 2
    for x, y in points:
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=colour)


def _rounded_corners(image, radius):
    """
    Cut the corners by painting background over them.

    The icon is opaque, so there is nothing to be gained from an alpha mask: a
    rounded rectangle drawn in the background colour over a square of the
    background colour is the same pixels, and it keeps the file in RGB. A
    transparent corner would show through as white on Android's own background
    anyway.
    """
    width, height = image.size
    corners = Image.new("L", image.size, 255)
    ImageDraw.Draw(corners).rounded_rectangle(
        [0, 0, width - 1, height - 1], radius=radius, fill=0
    )
    image.paste(Image.new("RGB", image.size, BACKGROUND), (0, 0), corners)
