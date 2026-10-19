import io
from pathlib import Path

import httpx
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from app.core.config import settings
from app.services.storage import LocalStorage


class PhotoEditorService:
    FILTERS = {
        "none": lambda img: img,
        "vivid": lambda img: ImageEnhance.Color(img).enhance(1.35),
        "mono": lambda img: ImageOps.grayscale(img).convert("RGB"),
        "soft": lambda img: img.filter(ImageFilter.SMOOTH_MORE),
        "dramatic": lambda img: ImageEnhance.Contrast(img).enhance(1.35),
    }

    def __init__(self):
        self.storage = LocalStorage()

    def load_image(self, *, source_image: str | None, upload_bytes: bytes | None) -> Image.Image:
        if upload_bytes:
            return Image.open(io.BytesIO(upload_bytes)).convert("RGBA")

        if not source_image:
            raise ValueError("No source image provided")

        if source_image.startswith("http://") or source_image.startswith("https://"):
            with httpx.Client(timeout=40) as client:
                response = client.get(source_image)
                response.raise_for_status()
                return Image.open(io.BytesIO(response.content)).convert("RGBA")

        local_path = Path(source_image)
        if not local_path.exists():
            raise ValueError("Source image path is not available on the server")
        return Image.open(local_path).convert("RGBA")

    def remove_background(self, image: Image.Image) -> Image.Image:
        if not settings.photoroom_api_key:
            raise ValueError("PhotoRoom API key is missing. Configure PHOTOROOM_API_KEY.")

        payload = io.BytesIO()
        image.save(payload, format="PNG")
        payload.seek(0)

        with httpx.Client(timeout=90) as client:
            response = client.post(
                settings.photoroom_api_url,
                headers={"x-api-key": settings.photoroom_api_key},
                files={"image_file": ("listing.png", payload.getvalue(), "image/png")},
            )
            response.raise_for_status()
        return Image.open(io.BytesIO(response.content)).convert("RGBA")

    def apply_edits(
        self,
        image: Image.Image,
        *,
        brightness: float = 1.0,
        contrast: float = 1.0,
        filter_name: str = "none",
        crop_x: int | None = None,
        crop_y: int | None = None,
        crop_width: int | None = None,
        crop_height: int | None = None,
    ) -> Image.Image:
        edited = image

        if crop_width and crop_height:
            left = max(crop_x or 0, 0)
            top = max(crop_y or 0, 0)
            right = min(left + crop_width, edited.width)
            bottom = min(top + crop_height, edited.height)
            if right > left and bottom > top:
                edited = edited.crop((left, top, right, bottom))

        if abs(brightness - 1.0) > 0.001:
            edited = ImageEnhance.Brightness(edited).enhance(brightness)

        if abs(contrast - 1.0) > 0.001:
            edited = ImageEnhance.Contrast(edited).enhance(contrast)

        transform = self.FILTERS.get(filter_name, self.FILTERS["none"])
        edited = transform(edited)
        return edited

    def save_image(self, image: Image.Image, *, transparent: bool = False) -> str:
        output = io.BytesIO()
        extension = ".png" if transparent else ".jpg"
        if transparent:
            image.save(output, format="PNG")
        else:
            image.convert("RGB").save(output, format="JPEG", quality=92)
        return self.storage.save_bytes(output.getvalue(), extension=extension, prefix="edited")
