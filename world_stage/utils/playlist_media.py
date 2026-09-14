import json
from urllib.parse import urljoin, urlsplit
from urllib.request import urlopen

from .show_metadata import is_media_url


class PlaylistMediaError(ValueError):
    pass


def resolve_playlist_media(url: str) -> str:
    if not urlsplit(url).path.lower().endswith('.json') or not is_media_url(url):
        return url
    try:
        with urlopen(url, timeout=5) as response:
            manifest = json.loads(response.read(1024 * 1024))
        sources = manifest.get("sources") if isinstance(manifest, dict) else None
        if not isinstance(sources, list):
            raise PlaylistMediaError(f"No media sources in manifest: {url}")
        for source in sources:
            source_url = source.get("url") if isinstance(source, dict) else None
            if not isinstance(source_url, str) or not source_url.strip():
                continue
            resolved = urljoin(url, source_url)
            if urlsplit(resolved).scheme in ("http", "https"):
                return resolved
    except (OSError, ValueError, TypeError) as exc:
        raise PlaylistMediaError(f"Cannot load media manifest: {url}") from exc
    raise PlaylistMediaError(f"No media source in manifest: {url}")
