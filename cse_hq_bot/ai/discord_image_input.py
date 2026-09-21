import asyncio
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol

from cse_hq_bot.ai.base import AIImage
from cse_hq_bot.errors import InvalidInputError

SUPPORTED_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
SUPPORTED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}
MAX_IMAGES_PER_MESSAGE = 3
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 12 * 1024 * 1024
ATTACHMENT_READ_TIMEOUT_SECONDS = 10


class DiscordAttachmentLike(Protocol):
    filename: str
    size: int
    content_type: str | None

    async def read(self, *, use_cached: bool = False) -> bytes:
        ...


async def extract_ai_images(
    attachments: Iterable[DiscordAttachmentLike],
) -> list[AIImage]:
    attachment_list = list(attachments)
    candidates = [
        attachment
        for attachment in attachment_list
        if _looks_like_supported_image(attachment)
    ]

    if not candidates:
        if attachment_list:
            raise InvalidInputError(
                "AI Vision v1 supports PNG, JPEG, and WEBP image attachments only."
            )
        return []

    if len(candidates) > MAX_IMAGES_PER_MESSAGE:
        raise InvalidInputError(
            f"AI Vision v1 supports at most {MAX_IMAGES_PER_MESSAGE} images per message."
        )

    images: list[AIImage] = []
    declared_total_bytes = 0
    actual_total_bytes = 0
    for attachment in candidates:
        declared_size = int(getattr(attachment, "size", 0) or 0)
        if declared_size <= 0:
            raise InvalidInputError(
                f"Image attachment {attachment.filename!r} has an invalid size."
            )
        if declared_size > MAX_IMAGE_BYTES:
            raise InvalidInputError(
                f"Image attachment {attachment.filename!r} exceeds the 8 MB per-image limit."
            )
        declared_total_bytes += declared_size
        if declared_total_bytes > MAX_TOTAL_IMAGE_BYTES:
            raise InvalidInputError(
                "Image attachments exceed the 12 MB total limit for one AI request."
            )

        try:
            data = await asyncio.wait_for(
                attachment.read(use_cached=True),
                timeout=ATTACHMENT_READ_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise InvalidInputError(
                f"Timed out while reading image attachment {attachment.filename!r}."
            ) from exc
        except Exception as exc:
            raise InvalidInputError(
                f"Unable to read image attachment {attachment.filename!r}."
            ) from exc

        if not data or len(data) > MAX_IMAGE_BYTES:
            raise InvalidInputError(
                f"Image attachment {attachment.filename!r} is empty or too large."
            )
        actual_total_bytes += len(data)
        if actual_total_bytes > MAX_TOTAL_IMAGE_BYTES:
            raise InvalidInputError(
                "Image attachments exceed the 12 MB total limit for one AI request."
            )

        detected_mime = _detect_image_mime(data)
        if detected_mime is None:
            raise InvalidInputError(
                f"Image attachment {attachment.filename!r} is not a valid PNG, JPEG, or WEBP file."
            )

        declared_mime = _normalized_content_type(attachment.content_type)
        if declared_mime in SUPPORTED_IMAGE_MIME_TYPES and declared_mime != detected_mime:
            raise InvalidInputError(
                f"Image attachment {attachment.filename!r} does not match its declared media type."
            )

        images.append(
            AIImage(
                data=data,
                mime_type=detected_mime,
                filename=attachment.filename,
            )
        )

    return images


def _looks_like_supported_image(attachment: DiscordAttachmentLike) -> bool:
    content_type = _normalized_content_type(attachment.content_type)
    suffix = Path(attachment.filename).suffix.lower()
    if content_type and content_type.startswith("image/"):
        if content_type not in SUPPORTED_IMAGE_MIME_TYPES:
            raise InvalidInputError(
                f"Unsupported image type {content_type!r}; use PNG, JPEG, or WEBP."
            )
        return True
    return suffix in SUPPORTED_IMAGE_EXTENSIONS


def _normalized_content_type(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(";", 1)[0].strip().lower() or None


def _detect_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
