from pathlib import Path

from PIL import Image, ImageEnhance


class ImagePipelineService:
    def process(self, file_path: str) -> str:
        source = Path(file_path)
        target = source.with_name(f"processed_{source.name}")
        with Image.open(source) as img:
            w, h = img.size
            size = min(w, h)
            left = (w - size) // 2
            top = (h - size) // 2
            cropped = img.crop((left, top, left + size, top + size))
            enhanced = ImageEnhance.Contrast(cropped).enhance(1.05)
            enhanced.save(target)
        return str(target)
