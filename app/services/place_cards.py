"""Venue-card selection for Travel chat (D1) and the catalogue swap (D4).

This module is intentionally separate from ``app/services/unified_chat.py``:
card selection is a deterministic, data-driven lookup keyed by destination and
a small set of intent keywords, and it must never influence the LLM prompt or
grounding. ``app/api/unified.py`` calls :func:`select_place_cards` after the
reply has already been planned/streamed and merges the result into the SSE
``done`` payload as an additional, optional field.

The catalogue itself (``datasets/demo/catalogue.json`` by default) is treated
as untrusted input from the app's point of view: every media and review entry
must declare a complete licence block (``license.type`` plus the three
``can_*`` booleans described in ``datasets/README.md``). A file missing any of
that -- including a real-data file dropped in without running
``scripts/validate_catalogue.py`` first -- makes the loader disable cards
entirely (fail closed) rather than assume a permissive default. This mirrors
the checks in ``scripts/validate_catalogue.py`` but is a small, dependency-free
runtime subset: the script is the authority on whether a file is fit to ship,
this loader is only the last guard before the app reads it.
"""
from __future__ import annotations

import json
import logging
import threading
import unicodedata
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LICENSE_FLAGS = ("can_store", "can_redistribute", "can_train")
_MAX_CARDS = 3
_REVIEW_SNIPPET_MAX_CHARS = 160
_BUDGET_MAX_AMOUNT = 50_000


class CatalogueState:
    """The parsed, licence-checked catalogue plus the media lookup index."""

    def __init__(
        self,
        *,
        destinations: dict[str, dict],
        venues: list[dict],
        media_index: dict[str, Path],
        media_root: Path,
    ) -> None:
        self.destinations = destinations
        self.venues = venues
        self.media_index = media_index
        self.media_root = media_root


_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"key": None, "state": None}


def _catalogue_file_path() -> Path:
    raw = settings.catalogue_path
    path = Path(raw)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


def _fold(value: str) -> str:
    """Lowercase and strip diacritics while keeping non-Latin scripts intact.

    Vietnamese keyword matching needs diacritics removed; Chinese keyword
    matching needs the characters left alone. NFD + combining-mark removal
    does both: it only ever touches characters that decompose.
    """
    value = unicodedata.normalize("NFD", value.casefold())
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return value.replace("đ", "d")


def _has_required_license(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    if not block.get("type"):
        return False
    for flag in _LICENSE_FLAGS:
        if not isinstance(block.get(flag), bool):
            return False
    return True


def _validate_license_flags(catalogue: dict) -> bool:
    """Same rule as ``scripts/validate_catalogue.py``: no flag, no default."""
    venues = catalogue.get("venues")
    if not isinstance(venues, list) or not venues:
        return False
    for venue in venues:
        if not isinstance(venue, dict):
            return False
        for group in ("media", "reviews"):
            items = venue.get(group)
            if not isinstance(items, list):
                return False
            for item in items:
                if not isinstance(item, dict) or not _has_required_license(item.get("license")):
                    return False
    return True


def _build_media_index(venues: list[dict], media_root: Path) -> dict[str, Path]:
    """Basename -> absolute path, for entries that resolve inside media_root.

    An entry whose declared URI would resolve outside the catalogue's own
    media directory is dropped rather than indexed, so a corrupted or
    maliciously edited catalogue file can never make the media route serve
    anything outside its allowlisted directory.
    """
    index: dict[str, Path] = {}
    for venue in venues:
        for item in venue.get("media") or []:
            uri = item.get("uri")
            if not isinstance(uri, str) or not uri:
                continue
            name = Path(uri).name
            try:
                resolved = (PROJECT_ROOT / uri).resolve()
            except OSError:
                continue
            if not resolved.is_relative_to(media_root):
                logger.warning(
                    "Catalogue media entry %r resolves outside %s; skipping.",
                    uri,
                    media_root,
                )
                continue
            index.setdefault(name, resolved)
    return index


def _load_catalogue_state() -> CatalogueState | None:
    if not settings.catalogue_enabled:
        return None
    path = _catalogue_file_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        logger.error("Place catalogue not found at %s; place cards are disabled.", path)
        return None

    cache_key = (str(path), mtime, settings.catalogue_enabled)
    with _cache_lock:
        if _cache["key"] == cache_key:
            return _cache["state"]

    state = _parse_catalogue_state(path)
    with _cache_lock:
        _cache["key"] = cache_key
        _cache["state"] = state
    return state


def _parse_catalogue_state(path: Path) -> CatalogueState | None:
    try:
        with path.open("r", encoding="utf-8") as stream:
            catalogue = json.load(stream)
    except (OSError, ValueError) as exc:
        logger.error("Place catalogue at %s could not be read (%s); place cards are disabled.", path, exc)
        return None
    if not isinstance(catalogue, dict) or not _validate_license_flags(catalogue):
        logger.error(
            "Place catalogue at %s is missing required licence flags on one or more "
            "media/review entries; place cards are disabled until the file passes "
            "scripts/validate_catalogue.py.",
            path,
        )
        return None
    venues = [venue for venue in catalogue.get("venues", []) if isinstance(venue, dict)]
    destinations = {
        item.get("slug"): item
        for item in catalogue.get("destinations", [])
        if isinstance(item, dict) and item.get("slug")
    }
    media_root = (path.parent / "media").resolve()
    return CatalogueState(
        destinations=destinations,
        venues=venues,
        media_index=_build_media_index(venues, media_root),
        media_root=media_root,
    )


def get_catalogue_state() -> CatalogueState | None:
    """Public accessor used by the media route; ``None`` means fail closed."""
    return _load_catalogue_state()


def reset_cache_for_tests() -> None:
    with _cache_lock:
        _cache["key"] = None
        _cache["state"] = None


# ---------------------------------------------------------------------------
# Destination and intent detection
# ---------------------------------------------------------------------------

def _destination_name_variants(destination: dict) -> list[str]:
    variants: list[str] = []
    slug = destination.get("slug")
    if isinstance(slug, str) and slug:
        variants.append(slug.replace("demo-", "").replace("-", " "))
    name = destination.get("name")
    if isinstance(name, dict):
        for value in name.values():
            if isinstance(value, str) and value:
                # Strip "(demo)" / full-width parenthetical glosses so the bare
                # place name is what gets matched against guest text.
                cleaned = value.split("(")[0].split("（")[0].strip()
                if cleaned:
                    variants.append(cleaned)
    return [_fold(variant) for variant in variants if variant]


def _detect_destination_slug(folded_message: str, state: CatalogueState) -> str | None:
    best_slug: str | None = None
    best_length = 0
    for slug, destination in state.destinations.items():
        for variant in _destination_name_variants(destination):
            if len(variant) > best_length and variant in folded_message:
                best_slug, best_length = slug, len(variant)
    return best_slug


_INTENT_DEFS: dict[str, dict[str, Any]] = {
    "food": {
        "keywords": (
            "an gi", "cho an", "an ngon", "do an", "mon an", "mon ngon", "am thuc",
            "quan an", "nha hang", "an uong", "quan ca phe",
            "food", "where to eat", "good food", "eat", "restaurant", "dining", "meal",
            "吃", "美食", "餐厅", "小吃",
        ),
        "match": lambda venue: venue.get("category") in {"food", "cafe", "market"}
        or "food" in (venue.get("tags") or []),
    },
    "sights": {
        "keywords": (
            "tham quan", "ngam canh", "canh dep", "cho choi", "chup anh", "check in",
            "sightsee", "viewpoint", "scenic", "attraction", "things to do",
            "景点", "游览", "观光", "打卡",
        ),
        "match": lambda venue: venue.get("category")
        in {"viewpoint", "garden", "museum", "walk", "water", "craft"}
        or "scenic" in (venue.get("tags") or [])
        or "photo_spot" in (venue.get("tags") or []),
    },
    "indoor": {
        "keywords": (
            "troi mua", "neu mua", "trong nha", "che mua", "tranh nang",
            "rain", "rainy", "indoor", "raining",
            "下雨", "室内", "雨天",
        ),
        "match": lambda venue: bool(venue.get("indoor"))
        or "indoor" in (venue.get("tags") or [])
        or "rainy_day" in (venue.get("tags") or []),
    },
    "kid_friendly": {
        "keywords": (
            "tre em", "tre nho", "gia dinh", "con nho", "cho tre con",
            "kid", "kids", "child", "children", "family friendly",
            "孩子", "小孩", "家庭出游", "儿童",
        ),
        "match": lambda venue: "family_friendly" in (venue.get("tags") or []),
    },
    "budget": {
        "keywords": (
            "gia re", "tiet kiem", "ngan sach", "mien phi", "gia phai chang",
            "budget", "cheap", "affordable", "low cost",
            "便宜", "预算", "免费", "划算",
        ),
        "match": lambda venue: "budget" in (venue.get("tags") or [])
        or "free" in (venue.get("tags") or [])
        or (
            isinstance(venue.get("price"), dict)
            and isinstance(venue["price"].get("amount"), int)
            and venue["price"]["amount"] <= _BUDGET_MAX_AMOUNT
        ),
    },
}

_GENERIC_PLACE_KEYWORDS = (
    "dia diem", "cho choi la gi", "goi y cho toi", "di dau choi", "noi nao nen den",
    "place to go", "places to visit", "recommend a spot", "suggestion for a place",
    "where should i go", "地方推荐", "去哪里玩", "推荐一个地方",
)


def _matched_intents(folded_message: str) -> list[str]:
    return [
        name
        for name, spec in _INTENT_DEFS.items()
        if any(keyword in folded_message for keyword in spec["keywords"])
    ]


def _mentions_generic_places(folded_message: str) -> bool:
    return any(keyword in folded_message for keyword in _GENERIC_PLACE_KEYWORDS)


def _rating_average(venue: dict) -> float:
    rating = venue.get("rating")
    value = rating.get("average") if isinstance(rating, dict) else None
    return float(value) if isinstance(value, (int, float)) else 0.0


def _rating_count(venue: dict) -> int:
    rating = venue.get("rating")
    value = rating.get("count") if isinstance(rating, dict) else None
    return int(value) if isinstance(value, int) else 0


# ---------------------------------------------------------------------------
# Card formatting
# ---------------------------------------------------------------------------

def _localized(value: Any, locale: str) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        candidate = value.get(locale) or value.get("vi")
        if isinstance(candidate, str) and candidate:
            return candidate
        for fallback in value.values():
            if isinstance(fallback, str) and fallback:
                return fallback
    return None


def _cover_media(media: Any) -> dict | None:
    if not isinstance(media, list):
        return None
    for item in media:
        if isinstance(item, dict) and item.get("role") == "cover":
            return item
    for item in media:
        if isinstance(item, dict):
            return item
    return None


def _review_snippet(reviews: Any) -> str | None:
    if not isinstance(reviews, list) or not reviews:
        return None
    candidates = [
        review
        for review in reviews
        if isinstance(review, dict) and isinstance(review.get("text"), str) and review["text"].strip()
    ]
    if not candidates:
        return None

    def sort_key(review: dict) -> tuple[int, int]:
        rating = review.get("rating")
        helpful = review.get("helpful_count")
        return (
            -(rating if isinstance(rating, int) else 0),
            -(helpful if isinstance(helpful, int) else 0),
        )

    best = sorted(candidates, key=sort_key)[0]
    text = best["text"].strip()
    if len(text) <= _REVIEW_SNIPPET_MAX_CHARS:
        return text
    return text[: _REVIEW_SNIPPET_MAX_CHARS - 1].rstrip() + "…"


def _safe_coordinates(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    lat, lon = value.get("lat"), value.get("lon")
    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and -90 <= lat <= 90 and -180 <= lon <= 180:
        return {"lat": float(lat), "lon": float(lon)}
    return None


def _build_card(venue: dict, locale: str, state: CatalogueState) -> dict:
    media_item = _cover_media(venue.get("media"))
    image_url = None
    image_alt = None
    if media_item:
        uri = media_item.get("uri")
        name = Path(uri).name if isinstance(uri, str) else None
        if name and name in state.media_index:
            image_url = f"/api/v1/media/catalogue/{name}"
        image_alt = _localized(media_item.get("alt"), locale)
    license_block = media_item.get("license") if isinstance(media_item, dict) else None
    license_type = license_block.get("type") if isinstance(license_block, dict) else None
    return {
        "id": venue.get("id"),
        "name": _localized(venue.get("name"), locale) or venue.get("slug") or venue.get("id"),
        "category": venue.get("category") if isinstance(venue.get("category"), str) else None,
        "image_url": image_url,
        "image_alt": image_alt,
        "rating": {
            "average": _rating_average(venue) if venue.get("rating") else None,
            "count": _rating_count(venue),
        },
        "review_snippet": _review_snippet(venue.get("reviews")),
        "coordinates": _safe_coordinates(venue.get("coordinates")),
        "license_type": license_type if isinstance(license_type, str) else None,
    }


# ---------------------------------------------------------------------------
# Public selection entry point
# ---------------------------------------------------------------------------

def select_place_cards(
    message: str,
    *,
    mode: str,
    destination_hint: str | None = None,
    locale: str = "vi",
    limit: int = _MAX_CARDS,
) -> list[dict]:
    """Up to ``limit`` venue cards for a Travel-mode message, or ``[]``.

    Selection is destination + intent-keyword + attribute matching over the
    loaded catalogue, entirely independent of the LLM reply: nothing here
    reads or writes ``UnifiedChatPlan.llm_messages``. Returns ``[]`` whenever
    the mode is not TRAVEL, the catalogue is disabled/invalid, no destination
    from the demo catalogue is named in the message, or intent keywords were
    used but matched no venue.
    """
    if mode != "TRAVEL" or not message:
        return []
    state = _load_catalogue_state()
    if state is None or not state.venues:
        return []

    folded_message = _fold(message)
    destination_slug = destination_hint if destination_hint in state.destinations else None
    if destination_slug is None:
        destination_slug = _detect_destination_slug(folded_message, state)
    if destination_slug is None:
        return []

    candidates = [venue for venue in state.venues if venue.get("destination_slug") == destination_slug]
    if not candidates:
        return []

    matched_intents = _matched_intents(folded_message)
    if matched_intents:
        scored: list[tuple[int, dict]] = []
        for venue in candidates:
            score = sum(1 for name in matched_intents if _INTENT_DEFS[name]["match"](venue))
            if score > 0:
                scored.append((score, venue))
        if not scored:
            # Keywords were used but nothing in this destination matches them:
            # showing unrelated venues would misrepresent the answer.
            return []
        scored.sort(key=lambda pair: (-pair[0], -_rating_average(pair[1]), -_rating_count(pair[1])))
        selected = [venue for _, venue in scored[:limit]]
    elif _mentions_generic_places(folded_message):
        ranked = sorted(candidates, key=lambda venue: (-_rating_average(venue), -_rating_count(venue)))
        selected = ranked[:limit]
    else:
        # A destination was named but nothing indicates a "places" question
        # (e.g. a policy or logistics question that happens to mention it).
        return []

    return [_build_card(venue, locale, state) for venue in selected]
