import colorsys
import hashlib
import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage

MAX_AVATAR_DIMENSION = 512
MAX_AVATAR_BYTES = 2 * 1024 * 1024
ALLOWED_AVATAR_FORMATS = {"JPEG", "PNG", "WEBP"}


class AvatarValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class UploadedAvatar:
    data: bytes
    media_type: str
    width: int
    height: int


def normalize_avatar(upload: FileStorage) -> UploadedAvatar:
    raw = upload.stream.read(MAX_AVATAR_BYTES + 1)
    if not raw:
        raise AvatarValidationError("Choose an image to upload.")
    if len(raw) > MAX_AVATAR_BYTES:
        raise AvatarValidationError("Avatar files must be no larger than 2 MB.")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(BytesIO(raw))
            if image.format not in ALLOWED_AVATAR_FORMATS:
                raise AvatarValidationError("Upload a PNG, JPEG, or WebP image.")
            if getattr(image, "n_frames", 1) != 1:
                raise AvatarValidationError("Animated avatars are not supported.")
            if max(image.size) > MAX_AVATAR_DIMENSION:
                raise AvatarValidationError(
                    "Avatar dimensions must not exceed 512 × 512 pixels."
                )
            image.load()
    except AvatarValidationError:
        raise
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
    ):
        raise AvatarValidationError("Upload a valid PNG, JPEG, or WebP image.") from None

    transposed = ImageOps.exif_transpose(image)
    assert transposed is not None
    image = transposed
    if max(image.size) > MAX_AVATAR_DIMENSION:
        raise AvatarValidationError("Avatar dimensions must not exceed 512 × 512 pixels.")

    has_transparency = image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    )
    image = image.convert("RGBA" if has_transparency else "RGB")
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)

    return UploadedAvatar(
        data=output.getvalue(),
        media_type="image/png",
        width=image.width,
        height=image.height,
    )


def default_avatar_svg(user_id: int) -> bytes:
    digest = hashlib.sha256(f"world-stage-avatar:{user_id}".encode("ascii")).digest()
    hue = int.from_bytes(digest[:2], "big") / 65535
    red, green, blue = colorsys.hls_to_rgb(hue, 0.43, 0.68)
    colour = f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"

    cells: list[tuple[int, int]] = []
    for row in range(5):
        for column in range(3):
            if digest[2 + row * 3 + column] & 1:
                cells.append((column + 1, row + 1))
                mirrored_column = 5 - column
                if mirrored_column != column + 1:
                    cells.append((mirrored_column, row + 1))
    if not cells:
        cells.append((3, 3))

    rectangles = "".join(
        f'<rect x="{column}" y="{row}" width="1" height="1"/>'
        for column, row in cells
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" '
        'viewBox="0 0 7 7" shape-rendering="crispEdges">'
        '<rect width="7" height="7" fill="#f0f0f0"/>'
        f'<g fill="{colour}">{rectangles}</g></svg>'
    ).encode("ascii")
