"""
Image pipeline.

Every uploaded image is re-encoded before it touches storage. Three reasons,
in order of importance:

1. Data cost. Our users pay for bandwidth by the megabyte. A 4MB phone photo
   served as-is to 200 feed viewers is R-something of somebody else's airtime.
2. Privacy. Phone cameras embed GPS coordinates in EXIF. Publishing a car photo
   taken at home would broadcast the owner's home address. Re-encoding through
   Pillow drops all EXIF, which is the behaviour we want. This is not optional
   under POPIA.
3. Storage cost and predictability.

Use `process_upload()` in every form that accepts an image.
"""
import io
import logging
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = 80_000_000  # decompression bomb guard


class ImageProcessingError(ValidationError):
    pass


def process_upload(uploaded_file, *, max_edge=None, quality=None, prefix="img"):
    """
    Validate and re-encode an uploaded image.

    Returns (display_file, thumb_file) as ContentFile objects ready to assign
    to an ImageField. Both are WebP.
    """
    max_edge = max_edge or settings.IMAGE_MAX_EDGE
    quality = quality or settings.IMAGE_QUALITY

    size = getattr(uploaded_file, "size", None)
    if size and size > settings.IMAGE_MAX_UPLOAD_BYTES:
        limit_mb = settings.IMAGE_MAX_UPLOAD_BYTES // (1024 * 1024)
        raise ImageProcessingError(f"That image is too large. Maximum {limit_mb}MB.")

    try:
        uploaded_file.seek(0)
        img = Image.open(uploaded_file)
        img.verify()          # cheap structural check
        uploaded_file.seek(0)
        img = Image.open(uploaded_file)  # verify() leaves the file unusable
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.info("Rejected upload: %s", exc)
        raise ImageProcessingError("That file doesn't look like an image we can read.")

    if img.format not in settings.IMAGE_ALLOWED_FORMATS:
        raise ImageProcessingError(
            "Please upload a JPG, PNG or WebP image."
        )

    # Honour the EXIF rotation flag, then discard EXIF entirely.
    img = ImageOps.exif_transpose(img)

    if img.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        converted = img.convert("RGBA")
        background.paste(converted, mask=converted.split()[-1])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    stem = f"{prefix}_{uuid.uuid4().hex[:12]}"
    display = _encode(_fit(img, max_edge), quality, f"{stem}.webp")
    thumb = _encode(_fit(img, settings.IMAGE_THUMB_EDGE), quality - 8, f"{stem}_t.webp")
    return display, thumb


def _fit(img, max_edge):
    """Downscale so the long edge is at most max_edge. Never upscale."""
    if max(img.size) <= max_edge:
        return img.copy()
    resized = img.copy()
    resized.thumbnail((max_edge, max_edge), Image.LANCZOS)
    return resized


def _encode(img, quality, name):
    buffer = io.BytesIO()
    img.save(buffer, format="WEBP", quality=max(40, min(95, quality)), method=4)
    buffer.seek(0)
    return ContentFile(buffer.read(), name=name)
