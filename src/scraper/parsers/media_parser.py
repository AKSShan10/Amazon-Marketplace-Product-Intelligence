"""
Image and video extraction.

Images: the rendered src is frequently a downscaled variant. The full-size URL
lives in data-old-hires, or inside the data-a-dynamic-image JSON map from which
the largest entry is taken. Feature C clusters these, so resolution matters.

Video: the direct media URL is usually inside a JSON blob consumed by Amazon's
player rather than in clean markup, and those CDN URLs are often signed and
time-limited. Three fields are therefore stored:

    has_video       always reliable
    video_url       direct URL if extractable; nulled later if it fails validation
    video_page_url  the product URL - always works, video plays there
"""
from __future__ import annotations

import re
from typing import Optional

from src.common.utils import any_present, first_element, largest_from_dynamic_image
from src.scraper import selectors

_MP4_PATTERN = re.compile(r"https://[^\"'\s\\]+?\.mp4")
_CDN_HINTS = ("media-amazon", "ssl-images-amazon", "images-amazon")


def parse_images(soup) -> dict:
    element = first_element(soup, selectors.MAIN_IMAGE)
    if element is None:
        return {"image_url": None, "image_url_hires": None}

    standard = element.get("src")
    hires: Optional[str] = None

    for attr in selectors.IMAGE_HIRES_ATTRS:
        value = element.get(attr)
        if not value:
            continue
        if attr == "data-a-dynamic-image":
            hires = largest_from_dynamic_image(value)
        else:
            hires = value
        if hires:
            break

    return {
        "image_url": standard or hires,
        "image_url_hires": hires or standard,
    }


def parse_video(html: str, soup, product_url: str) -> dict:
    has_video = any_present(soup, selectors.VIDEO_CONTAINER)

    video_url: Optional[str] = None

    # 1. direct attribute on a container element
    element = first_element(soup, selectors.VIDEO_CONTAINER)
    if element is not None:
        for attr in selectors.VIDEO_URL_ATTR:
            value = element.get(attr)
            if value and value.startswith("http"):
                video_url = value
                break

    # 2. fall back to the media JSON embedded in the page source
    if video_url is None:
        candidates = [
            url for url in _MP4_PATTERN.findall(html)
            if any(hint in url for hint in _CDN_HINTS)
        ]
        if candidates:
            video_url = candidates[0]
            has_video = True

    return {
        "has_video": bool(has_video or video_url),
        "video_url": video_url,
        "video_page_url": product_url,
    }
