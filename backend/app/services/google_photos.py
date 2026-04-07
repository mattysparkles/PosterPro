import re

import httpx


class GooglePhotosService:
    IMG_PATTERN = re.compile(r'https://lh3\.googleusercontent\.com/[\w\-=]+')

    def extract_image_urls(self, album_url: str) -> list[str]:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            html = client.get(album_url).text
        urls = sorted(set(self.IMG_PATTERN.findall(html)))
        return urls
