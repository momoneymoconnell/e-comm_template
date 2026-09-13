"""Image upload, processing and storage.

**What "image hosting" actually means here.** Product images are files, and
files have to live somewhere that survives a container restart. This module
writes them to a directory that is a Docker volume, serves them back over HTTP,
and gives them content-addressed names so they can be cached forever.

That is the whole of it. No external account, no S3 bucket, no CDN required to
start. On a single cloud host the same volume works unchanged. `MediaStorage`
below is a protocol with one local implementation, so putting the files in S3,
R2 or a CDN later means adding one class and changing a setting, not touching
any calling code.

**Every upload is re-encoded rather than stored as received.** That is
deliberate and does several jobs at once:

* Strips EXIF. Phone photos routinely carry GPS coordinates, and a shop
  publishing the seller's home address in its product images is a real and
  frequently repeated mistake.
* Defuses polyglot files. A file can be a valid GIF *and* a valid HTML document
  at the same time; browsers have been known to execute the HTML half. Decoding
  and re-encoding produces clean output that is only an image.
* Bounds the size. An 8 MB camera original becomes a ~200 KB web image, which
  matters more for a storefront's conversion rate than almost anything else on
  the page.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ecom_shared.errors import ValidationFailedError
from ecom_shared.logging import get_logger
from PIL import Image, UnidentifiedImageError

log = get_logger(__name__)

#: Formats accepted on upload, mapped to the format they are stored as.
#:
#: A closed allowlist, checked against what Pillow actually decodes rather than
#: against the declared content type or the file extension, both of which are
#: supplied by the client and neither of which means anything.
#:
#: SVG is deliberately absent. SVG is XML, it can contain `<script>`, and
#: serving one from your own origin is a stored-XSS vector. Anyone needing
#: vector art can export a PNG.
ACCEPTED_FORMATS: dict[str, str] = {
    "JPEG": "JPEG",
    "PNG": "PNG",
    "WEBP": "WEBP",
    "GIF": "PNG",  # flattened; animation is not worth the complexity here
}

#: Output type per stored format.
CONTENT_TYPES: dict[str, str] = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}

#: Longest edge of the stored full-size image, in pixels.
#:
#: 1600 is comfortably enough for a full-bleed product shot on a high-density
#: display, and roughly an order of magnitude smaller than what a modern phone
#: camera produces.
MAX_EDGE = 1600

#: Longest edge of the generated thumbnail, used in grids and the admin list.
THUMB_EDGE = 400

#: Refuse anything larger before decoding it.
#:
#: A "decompression bomb" is a small file that expands to an enormous bitmap;
#: Pillow has its own guard, and this is the cheaper first line of defence.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ProcessedImage:
    """A decoded, re-encoded image ready to be stored.

    Attributes:
        data: The full-size image bytes.
        thumb_data: The thumbnail bytes.
        content_type: MIME type of both.
        extension: File extension to store under, without the dot.
        width: Full-size width in pixels.
        height: Full-size height in pixels.
        checksum: SHA-256 of `data`, used as the filename so identical uploads
            deduplicate and the URL can be cached forever.
    """

    data: bytes
    thumb_data: bytes
    content_type: str
    extension: str
    width: int
    height: int
    checksum: str

    @property
    def filename(self) -> str:
        """Content-addressed filename for the full-size image."""
        return f"{self.checksum}.{self.extension}"

    @property
    def thumb_filename(self) -> str:
        """Content-addressed filename for the thumbnail."""
        return f"{self.checksum}_thumb.{self.extension}"


def process_upload(raw: bytes, *, original_name: str) -> ProcessedImage:
    """Validate, normalise and re-encode an uploaded image.

    Args:
        raw: The uploaded bytes, exactly as received.
        original_name: The client-supplied filename. Used only for the error
            message and for the stored record; never for the path on disk.

    Returns:
        The processed image and its derivatives.

    Raises:
        ValidationFailedError: If the file is too large, is not an image, or is
            an image in a format we do not accept.
    """
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValidationFailedError(
            f"That image is too large. The limit is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
            details={"filename": original_name, "bytes": len(raw)},
        )
    if not raw:
        raise ValidationFailedError("That file is empty.")

    try:
        # Opened twice on purpose: `verify()` consumes the file object and
        # leaves the image unusable, so the real decode needs a fresh stream.
        # Skipping verify and going straight to load would decode a malformed
        # file before noticing anything is wrong.
        Image.open(io.BytesIO(raw)).verify()
        # Annotated as the base `Image`, not the `ImageFile` that `open()`
        # returns: the conversions below produce plain `Image` objects, and
        # without this the first reassignment is a type error.
        image: Image.Image = Image.open(io.BytesIO(raw))
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationFailedError(
            "That file is not a readable image.", details={"filename": original_name}
        ) from exc

    source_format = (image.format or "").upper()
    if source_format not in ACCEPTED_FORMATS:
        raise ValidationFailedError(
            f"{source_format or 'That format'} is not supported. Use JPEG, PNG or WebP.",
            details={"filename": original_name, "format": source_format},
        )

    target_format = ACCEPTED_FORMATS[source_format]

    # Honour the EXIF orientation tag, then discard the EXIF entirely. Without
    # this step, photos taken in portrait on a phone appear rotated.
    from PIL import ImageOps

    image = ImageOps.exif_transpose(image) or image

    if target_format == "JPEG":
        # JPEG has no alpha channel. Compositing onto white rather than letting
        # Pillow drop the channel avoids transparent areas turning black.
        if image.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", image.size, (255, 255, 255))
            converted = image.convert("RGBA")
            background.paste(converted, mask=converted.split()[-1])
            image = background
        else:
            image = image.convert("RGB")
    elif image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA")

    full = _fit(image, MAX_EDGE)
    thumb = _fit(image, THUMB_EDGE)

    data = _encode(full, target_format)
    thumb_data = _encode(thumb, target_format)

    return ProcessedImage(
        data=data,
        thumb_data=thumb_data,
        content_type=CONTENT_TYPES[target_format],
        extension="jpg" if target_format == "JPEG" else target_format.lower(),
        width=full.width,
        height=full.height,
        # Hashing the *processed* bytes, not the upload, so two different
        # camera originals that normalise to the same image deduplicate.
        checksum=hashlib.sha256(data).hexdigest()[:32],
    )


def _fit(image: Image.Image, max_edge: int) -> Image.Image:
    """Scale an image down so its longest edge is at most `max_edge`.

    Never scales up. Enlarging a small image just makes a bigger blurry file.
    """
    if max(image.size) <= max_edge:
        return image.copy()

    resized = image.copy()
    # LANCZOS is the best-quality downscaling filter Pillow offers, and the
    # cost is irrelevant for a handful of admin uploads.
    resized.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    return resized


def _encode(image: Image.Image, target_format: str) -> bytes:
    """Encode an image to bytes with sensible web settings."""
    buffer = io.BytesIO()

    if target_format == "JPEG":
        # 85 is the usual sweet spot: visually indistinguishable from 100 at
        # roughly half the size. `optimize` runs a second Huffman pass;
        # `progressive` makes a large image appear blurry-then-sharp rather
        # than loading top to bottom.
        image.save(buffer, format="JPEG", quality=85, optimize=True, progressive=True)
    elif target_format == "WEBP":
        image.save(buffer, format="WEBP", quality=85, method=4)
    else:
        image.save(buffer, format="PNG", optimize=True)

    return buffer.getvalue()


class MediaStorage(Protocol):
    """Where processed images are kept.

    One method to write and one to read. Implementing this against S3, R2 or
    any other object store is a small, self-contained class; nothing that calls
    it needs to change.
    """

    def write(self, filename: str, data: bytes, content_type: str) -> None:
        """Store `data` under `filename`, overwriting any existing file."""
        ...

    def read(self, filename: str) -> bytes | None:
        """Return the stored bytes, or ``None`` if there is no such file."""
        ...

    def delete(self, filename: str) -> None:
        """Remove a file. Must not raise if it is already gone."""
        ...


class LocalMediaStorage:
    """Stores images on a local directory, which in Docker is a volume.

    Adequate for a single host, which is where this template is meant to run.
    Once you are running more than one replica the filesystem stops being
    shared and you want object storage behind the same interface.
    """

    def __init__(self, root: str) -> None:
        """Create the storage root if it does not exist.

        Args:
            root: Directory to write into.
        """
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, filename: str) -> Path:
        """Resolve a filename to a path inside the root.

        Args:
            filename: The stored filename.

        Returns:
            The absolute path.

        Raises:
            ValidationFailedError: If the name would escape the storage root.
                Filenames here are always generated from a hash, so this can
                only fire if something upstream is wrong — but path traversal
                is cheap to prevent and expensive to discover the hard way.
        """
        candidate = (self.root / filename).resolve()
        if not candidate.is_relative_to(self.root.resolve()):
            raise ValidationFailedError("Invalid media filename.")
        return candidate

    def write(self, filename: str, data: bytes, content_type: str) -> None:
        """Write bytes to disk atomically.

        Writes to a temporary file and renames it, so a crash mid-write cannot
        leave a truncated image being served. `rename` is atomic within a
        filesystem.
        """
        del content_type  # the local backend infers type on read from the name
        target = self._path(filename)
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.write_bytes(data)
        temporary.replace(target)

    def read(self, filename: str) -> bytes | None:
        """Read bytes from disk, or ``None`` if absent."""
        path = self._path(filename)
        if not path.is_file():
            return None
        return path.read_bytes()

    def delete(self, filename: str) -> None:
        """Delete a file, ignoring the case where it is already gone."""
        self._path(filename).unlink(missing_ok=True)


def content_type_for(filename: str) -> str:
    """Infer the response content type from a stored filename.

    Safe because every stored filename is generated by this module from a fixed
    set of extensions, never taken from the client.

    Args:
        filename: The stored filename.

    Returns:
        A MIME type, defaulting to ``application/octet-stream``.
    """
    suffix = filename.rsplit(".", 1)[-1].lower()
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }.get(suffix, "application/octet-stream")
