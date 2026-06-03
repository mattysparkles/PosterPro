import re

import httpx


class GooglePhotosService:
    IMG_PATTERN = re.compile(r'https://lh3\.googleusercontent\.com/[^\s"\']+')

    def extract_image_urls(self, album_url: str) -> list[str]:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            html = client.get(album_url).text
        urls = []
        for raw in self.IMG_PATTERN.findall(html):
            value = raw.replace("\\u003d", "=").replace("\\/", "/").strip()
            if not value.startswith("https://lh3.googleusercontent.com/"):
                continue
            # Shared albums contain non-image placeholder links; keep only actual renderable image URLs.
            if "=" not in value and "-no" not in value:
                continue
            urls.append(value)
        urls = sorted(set(urls))
        return urls
