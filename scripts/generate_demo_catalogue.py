"""Sinh catalogue demo có cấu trúc: địa điểm, dịch vụ, ảnh và đánh giá.

Bộ dữ liệu này phục vụ hai mục đích cùng lúc:

1. Trình diễn được các tính năng ảnh địa điểm, ảnh dịch vụ và đánh giá mà hiện
   chưa có chỗ chứa dữ liệu trong lược đồ cơ sở dữ liệu.
2. Đo được chất lượng gợi ý theo ngữ cảnh, bằng bộ câu hỏi vàng mô tả theo
   thuộc tính chứ không theo tên riêng, để phép đo vẫn còn giá trị sau khi thay
   toàn bộ nội dung demo bằng dữ liệu thật.

Kết quả là tất định: cùng một seed cho ra cùng một tệp, byte-for-byte. Nhờ vậy
mọi thay đổi trong catalogue đều hiện ra thành diff đọc được, và bài kiểm thử
hồi quy không bị nhiễu ngẫu nhiên.

Chỉ dùng thư viện chuẩn. `requirements.txt` là tệp giao thoa giữa hai người nên
không thêm phụ thuộc mới cho một công cụ sinh dữ liệu ngoại tuyến.

Xem `datasets/README.md` để biết vì sao catalogue này tách khỏi kho RAG.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import unicodedata
from pathlib import Path
from typing import Any

# Console Windows mặc định dùng bảng mã cp1252, không in được tiếng Việt và làm
# script dừng bằng UnicodeEncodeError ngay ở câu lệnh print cuối cùng, sau khi
# tệp đã ghi xong. Ép UTF-8 để công cụ chạy giống nhau trên Windows và Linux.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = PROJECT_ROOT / "datasets"
DEMO_ROOT = DATASET_ROOT / "demo"
MEDIA_ROOT = DEMO_ROOT / "media"
GOLDEN_ROOT = DATASET_ROOT / "golden"

DATASET_ID = "vietsage-demo-catalogue-2026-09"
SCHEMA_VERSION = "1.0"
DEFAULT_SEED = 20260921

# Giấy phép của nội dung tự sinh. Dữ liệu thật từ nhà cung cấp bên ngoài dùng
# đúng các trường này nhưng với giá trị khác; ứng dụng đọc cờ chứ không đọc tên
# nhà cung cấp, nên việc hoán đổi nguồn không phải sửa mã.
SYNTHETIC_LICENSE = {
    "type": "synthetic-demo",
    "can_store": True,
    "can_redistribute": True,
    "can_train": True,
    "display_attribution": None,
}

# ---------------------------------------------------------------------------
# Từ vựng sinh tên. Tên được ghép để nghe tự nhiên nhưng vẫn rõ ràng là hư cấu.
# ---------------------------------------------------------------------------

DESTINATIONS = [
    {"slug": "demo-hoa-mau", "vi": "Hoa Mẫu", "en": "Hoa Mau (demo)", "zh": "华茂（示例）",
     "province": "Tỉnh Mô Phỏng Bắc", "lat": 21.05, "lon": 105.80},
    {"slug": "demo-ben-lau", "vi": "Bến Lau", "en": "Ben Lau (demo)", "zh": "芦渡（示例）",
     "province": "Tỉnh Mô Phỏng Bắc", "lat": 20.62, "lon": 105.96},
    {"slug": "demo-suoi-trong", "vi": "Suối Trong", "en": "Suoi Trong (demo)", "zh": "清溪（示例）",
     "province": "Tỉnh Mô Phỏng Trung", "lat": 16.07, "lon": 108.15},
    {"slug": "demo-doi-cat-nho", "vi": "Đồi Cát Nhỏ", "en": "Doi Cat Nho (demo)", "zh": "小沙丘（示例）",
     "province": "Tỉnh Mô Phỏng Trung", "lat": 15.88, "lon": 108.33},
    {"slug": "demo-cua-nam", "vi": "Cửa Nam", "en": "Cua Nam (demo)", "zh": "南门（示例）",
     "province": "Tỉnh Mô Phỏng Nam", "lat": 10.78, "lon": 106.70},
    {"slug": "demo-rach-bang", "vi": "Rạch Bàng", "en": "Rach Bang (demo)", "zh": "榜涌（示例）",
     "province": "Tỉnh Mô Phỏng Nam", "lat": 10.41, "lon": 106.18},
]

NAME_HEAD = [
    "Bến", "Gác", "Nhà", "Vườn", "Chợ", "Quán", "Xưởng", "Đồi", "Bãi", "Hẻm",
    "Sân", "Lò", "Cầu", "Gò", "Bờ",
]
NAME_MID = [
    "Gió", "Mây", "Nước", "Lá", "Sương", "Nắng", "Đá", "Tre", "Sen", "Mưa",
    "Khói", "Cỏ", "Trăng", "Lửa", "Cát",
]
NAME_TAIL = [
    "Xanh", "Cũ", "Nhỏ", "Lặng", "Sớm", "Muộn", "Trong", "Mềm", "Vắng", "Thấp",
    "Rộng", "Ngắn", "Ấm", "Mát", "Sâu",
]

# ---------------------------------------------------------------------------
# Phân loại. Mỗi loại kèm sẵn các thuộc tính mà bộ câu hỏi vàng sẽ kiểm tra:
# trong nhà hay ngoài trời, hợp thời tiết nào, khoảng giá, khoảng thời lượng.
# ---------------------------------------------------------------------------

VENUE_CATEGORIES = [
    {
        "id": "viewpoint", "vi": "Điểm ngắm cảnh", "en": "Viewpoint", "zh": "观景点",
        "indoor": False, "weather_fit": ["clear", "light_cloud"],
        "price_range": (0, 40000), "duration_range": (30, 90),
        "tags": ["outdoor", "scenic", "photo_spot"],
    },
    {
        "id": "museum", "vi": "Nhà trưng bày", "en": "Exhibition house", "zh": "陈列馆",
        "indoor": True, "weather_fit": ["clear", "light_cloud", "rain", "hot", "cold"],
        "price_range": (20000, 120000), "duration_range": (45, 120),
        "tags": ["indoor", "culture", "family_friendly", "rainy_day"],
    },
    {
        "id": "food", "vi": "Quán ăn", "en": "Eatery", "zh": "小吃店",
        "indoor": True, "weather_fit": ["clear", "light_cloud", "rain", "hot", "cold"],
        "price_range": (25000, 180000), "duration_range": (30, 75),
        "tags": ["indoor", "food", "family_friendly"],
    },
    {
        "id": "cafe", "vi": "Quán nước", "en": "Cafe", "zh": "饮品店",
        "indoor": True, "weather_fit": ["clear", "light_cloud", "rain", "hot", "cold"],
        "price_range": (20000, 90000), "duration_range": (30, 120),
        "tags": ["indoor", "quiet", "rainy_day", "wifi"],
    },
    {
        "id": "market", "vi": "Chợ", "en": "Market", "zh": "市场",
        "indoor": False, "weather_fit": ["clear", "light_cloud"],
        "price_range": (0, 60000), "duration_range": (45, 120),
        "tags": ["outdoor", "food", "shopping", "crowded"],
    },
    {
        "id": "craft", "vi": "Xưởng thủ công", "en": "Craft workshop", "zh": "手工作坊",
        "indoor": True, "weather_fit": ["clear", "light_cloud", "rain", "hot", "cold"],
        "price_range": (50000, 250000), "duration_range": (60, 150),
        "tags": ["indoor", "culture", "hands_on", "family_friendly", "rainy_day"],
    },
    {
        "id": "walk", "vi": "Tuyến đi bộ", "en": "Walking route", "zh": "步行路线",
        "indoor": False, "weather_fit": ["clear", "light_cloud", "cold"],
        "price_range": (0, 0), "duration_range": (40, 180),
        "tags": ["outdoor", "free", "exercise"],
    },
    {
        "id": "water", "vi": "Điểm bơi lội", "en": "Swimming spot", "zh": "戏水点",
        "indoor": False, "weather_fit": ["clear", "hot"],
        "price_range": (0, 80000), "duration_range": (60, 180),
        "tags": ["outdoor", "water", "hot_day"],
    },
    {
        "id": "garden", "vi": "Vườn cây", "en": "Garden", "zh": "园圃",
        "indoor": False, "weather_fit": ["clear", "light_cloud", "cold"],
        "price_range": (0, 50000), "duration_range": (40, 100),
        "tags": ["outdoor", "quiet", "scenic", "family_friendly"],
    },
    {
        "id": "night", "vi": "Điểm về đêm", "en": "Night spot", "zh": "夜间场所",
        "indoor": False, "weather_fit": ["clear", "light_cloud"],
        "price_range": (30000, 200000), "duration_range": (60, 150),
        "tags": ["outdoor", "night_only", "food", "crowded"],
    },
]

SERVICE_CATEGORIES = [
    {"code": "laundry", "vi": "Giặt là", "en": "Laundry", "zh": "洗衣",
     "price": (40000, 160000), "minutes": (180, 480), "tags": ["room_service", "scheduled"]},
    {"code": "breakfast", "vi": "Bữa sáng tại phòng", "en": "In-room breakfast", "zh": "客房早餐",
     "price": (80000, 220000), "minutes": (20, 45), "tags": ["food", "scheduled"]},
    {"code": "shuttle", "vi": "Xe đưa đón", "en": "Shuttle", "zh": "接送车",
     "price": (100000, 400000), "minutes": (15, 60), "tags": ["transport", "scheduled"]},
    {"code": "late_checkout", "vi": "Trả phòng muộn", "en": "Late checkout", "zh": "延迟退房",
     "price": (0, 300000), "minutes": (0, 0), "tags": ["room", "request_only"]},
    {"code": "quiet_room", "vi": "Phòng đọc yên tĩnh", "en": "Quiet reading room", "zh": "安静阅览室",
     "price": (0, 0), "minutes": (0, 0), "tags": ["facility", "quiet", "free"]},
    {"code": "bike", "vi": "Thuê xe đạp", "en": "Bicycle rental", "zh": "自行车租借",
     "price": (50000, 150000), "minutes": (10, 20), "tags": ["transport", "outdoor"]},
]

# ---------------------------------------------------------------------------
# Từ vựng sinh đánh giá. Ghép mảnh để câu nghe tự nhiên mà vẫn tất định.
# ---------------------------------------------------------------------------

REVIEW_OPEN = {
    5: ["Đi đúng dịp nên rất ưng.", "Hơn mong đợi.", "Sẽ quay lại."],
    4: ["Nhìn chung là tốt.", "Đáng thời gian bỏ ra.", "Khá hài lòng."],
    3: ["Tạm được.", "Không có gì nổi bật.", "Đi một lần cho biết."],
    2: ["Hơi thất vọng.", "Chưa đáng công đi.", "Cần cải thiện nhiều."],
    1: ["Không nên đi vào lúc này.", "Trải nghiệm kém.", "Rất tiếc thời gian."],
}
REVIEW_DETAIL = [
    "Buổi sáng sớm vắng người, dễ chụp ảnh.",
    "Chiều muộn khá đông, nên đi sớm hơn.",
    "Chỗ để xe hơi khó tìm.",
    "Nhân viên hướng dẫn rõ ràng.",
    "Có mái che nên mưa vẫn đi được.",
    "Không có mái che, trời nắng thì mệt.",
    "Phù hợp cho trẻ nhỏ đi cùng.",
    "Lối đi hơi dốc, người lớn tuổi nên cân nhắc.",
    "Giá niêm yết rõ ràng.",
    "Nên mang theo nước uống.",
    "Khu vệ sinh sạch.",
    "Sóng điện thoại yếu ở phía trong.",
    "Có chỗ ngồi nghỉ dọc đường.",
    "Đi bộ từ bến xe khoảng mười lăm phút.",
]
REVIEW_CLOSE = [
    "Nói chung là nên thử.",
    "Tùy khẩu vị từng người.",
    "Đi cùng bạn bè thì vui hơn.",
    "Nên hỏi giờ mở cửa trước khi đi.",
    "",
]

AUTHOR_ALIAS_PREFIX = ["Khách", "Người đi đường", "Bạn đọc", "Lữ khách"]

# Bảng màu cho ảnh minh họa, chọn theo loại địa điểm để ảnh phân biệt được
# với nhau bằng mắt mà không cần tệp ảnh chụp thật.
CATEGORY_PALETTE = {
    "viewpoint": ("#1d4e5f", "#7fb6c4"),
    "museum": ("#4a3b62", "#b3a1cc"),
    "food": ("#7a3b22", "#e0a878"),
    "cafe": ("#5b4636", "#c8a983"),
    "market": ("#6b5300", "#d9c26a"),
    "craft": ("#2f5340", "#94c2a5"),
    "walk": ("#33502a", "#a3c78f"),
    "water": ("#17455e", "#76bcd6"),
    "garden": ("#2c5a33", "#9ccb9f"),
    "night": ("#25243f", "#8f8cc4"),
    "service": ("#3a3a3a", "#a8a8a8"),
}


def _slugify(value: str) -> str:
    folded = unicodedata.normalize("NFD", value.casefold())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    folded = folded.replace("đ", "d")
    return "".join(c if c.isalnum() else "_" for c in folded).strip("_")


def _money(rng: random.Random, low: int, high: int) -> int:
    """Giá được làm tròn về bội số 5.000 đồng cho giống bảng giá thật."""
    if high <= low:
        return low
    raw = rng.randint(low, high)
    return int(round(raw / 5000.0)) * 5000


# Đường chân trời khác nhau cho từng ảnh trong cùng một bộ sưu tập. Nếu mọi ảnh
# của một địa điểm giống hệt nhau thì bộ sưu tập không chứng minh được gì, và
# Git phải lưu nhiều tệp trùng nội dung.
_SKYLINES = (
    "M0 620 L240 470 L430 590 L700 400 L940 560 L1200 450 L1200 800 L0 800 Z",
    "M0 540 L180 600 L360 430 L620 580 L880 470 L1200 600 L1200 800 L0 800 Z",
    "M0 660 L300 520 L520 640 L760 500 L1010 620 L1200 520 L1200 800 L0 800 Z",
)
_SUN_POSITIONS = ((980, 180, 70), (220, 150, 54), (640, 130, 88))


def _svg(name: str, subtitle: str, palette: tuple[str, str], variant: int = 0) -> str:
    """Ảnh minh họa dạng vector.

    Dùng SVG thay vì ảnh chụp vì ba lý do: tệp nhỏ và diff đọc được trong Git,
    nội dung không vướng bản quyền, và kích thước cố định nên bố cục giao diện
    có thể kiểm thử tất định. Khi thay bằng ảnh thật, chỉ trường `uri` đổi.

    ``variant`` chọn bố cục khác nhau cho từng ảnh trong một bộ sưu tập.
    """
    dark, light = palette
    safe_name = name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    safe_sub = subtitle.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    skyline = _SKYLINES[variant % len(_SKYLINES)]
    sun_x, sun_y, sun_r = _SUN_POSITIONS[variant % len(_SUN_POSITIONS)]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 800" width="1200" height="800" '
        f'role="img" aria-label="{safe_name}">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{light}"/><stop offset="100%" stop-color="{dark}"/>'
        "</linearGradient></defs>"
        '<rect width="1200" height="800" fill="url(#g)"/>'
        f'<path d="{skyline}" fill="{dark}" opacity="0.55"/>'
        f'<circle cx="{sun_x}" cy="{sun_y}" r="{sun_r}" fill="#ffffff" opacity="0.28"/>'
        '<rect x="0" y="660" width="1200" height="140" fill="#000000" opacity="0.42"/>'
        f'<text x="60" y="720" font-family="Segoe UI, Arial, sans-serif" font-size="46" '
        f'fill="#ffffff">{safe_name}</text>'
        f'<text x="60" y="766" font-family="Segoe UI, Arial, sans-serif" font-size="26" '
        f'fill="#ffffff" opacity="0.85">{safe_sub}</text>'
        '<text x="1140" y="60" text-anchor="end" font-family="Segoe UI, Arial, sans-serif" '
        'font-size="22" fill="#ffffff" opacity="0.9">DEMO</text>'
        "</svg>"
    )


def _media_record(media_id: str, uri: str, alt_vi: str, alt_en: str, role: str) -> dict[str, Any]:
    return {
        "id": media_id,
        "kind": "photo",
        "role": role,
        "uri": uri,
        "width": 1200,
        "height": 800,
        "alt": {"vi": alt_vi, "en": alt_en},
        "license": dict(SYNTHETIC_LICENSE),
        "provenance": {
            "source": "synthetic-generator",
            "dataset_id": DATASET_ID,
            "fetched_at": None,
            "expires_at": None,
        },
    }


def _reviews(rng: random.Random, owner_id: str, count: int) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    for index in range(count):
        # Điểm số lệch về phía tích cực giống phân bố đánh giá thật, nhưng vẫn
        # có đủ điểm thấp để kiểm thử được cách hệ thống xử lý đánh giá xấu.
        rating = rng.choices([5, 4, 3, 2, 1], weights=[34, 30, 18, 11, 7])[0]
        sentences = [rng.choice(REVIEW_OPEN[rating])]
        for detail in rng.sample(REVIEW_DETAIL, rng.randint(1, 2)):
            sentences.append(detail)
        closing = rng.choice(REVIEW_CLOSE)
        if closing:
            sentences.append(closing)
        reviews.append({
            "id": f"review_{owner_id}_{index + 1:02d}",
            "rating": rating,
            "lang": "vi",
            "text": " ".join(sentences),
            "author_alias": f"{rng.choice(AUTHOR_ALIAS_PREFIX)} {rng.randint(10, 99)}",
            "visited_at": f"2026-{rng.randint(1, 9):02d}",
            "helpful_count": rng.randint(0, 24),
            "license": dict(SYNTHETIC_LICENSE),
            "provenance": {
                "source": "synthetic-generator",
                "dataset_id": DATASET_ID,
                "fetched_at": None,
                "expires_at": None,
            },
        })
    return reviews


def _rating_summary(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    if not reviews:
        return {"average": None, "count": 0, "distribution": {}}
    scores = [review["rating"] for review in reviews]
    distribution = {str(value): scores.count(value) for value in range(1, 6)}
    return {
        "average": round(sum(scores) / len(scores), 2),
        "count": len(scores),
        "distribution": distribution,
    }


def _venue_name(rng: random.Random, used: set[str]) -> str:
    for _ in range(200):
        name = f"{rng.choice(NAME_HEAD)} {rng.choice(NAME_MID)} {rng.choice(NAME_TAIL)}"
        if name not in used:
            used.add(name)
            return name
    raise RuntimeError("Không sinh đủ tên địa điểm không trùng nhau.")


def _hours(rng: random.Random, category: dict) -> dict[str, Any]:
    if category["id"] == "night":
        return {"open": "17:00", "close": "23:30", "closed_days": []}
    open_hour = rng.choice([5, 6, 7, 8, 9])
    close_hour = rng.choice([16, 17, 18, 19, 20])
    closed_days = [rng.choice(["monday", "tuesday", "wednesday"])] if rng.random() < 0.18 else []
    return {
        "open": f"{open_hour:02d}:{rng.choice(['00', '30'])}",
        "close": f"{close_hour:02d}:{rng.choice(['00', '30'])}",
        "closed_days": closed_days,
    }


def build_catalogue(seed: int, venues_per_destination: int) -> tuple[dict[str, Any], dict[str, str]]:
    """Dựng catalogue và các tệp ảnh đi kèm.

    Trả về ``(catalogue, media_files)`` trong đó ``media_files`` ánh xạ đường dẫn
    tương đối tới nội dung SVG, để hàm gọi quyết định lúc nào ghi xuống đĩa.
    """
    rng = random.Random(seed)
    used_names: set[str] = set()
    media_files: dict[str, str] = {}
    venues: list[dict[str, Any]] = []
    services: list[dict[str, Any]] = []

    for destination in DESTINATIONS:
        for index in range(venues_per_destination):
            category = VENUE_CATEGORIES[index % len(VENUE_CATEGORIES)]
            name_vi = _venue_name(rng, used_names)
            venue_id = f"venue_{destination['slug'].replace('-', '_')}_{index + 1:03d}"
            slug = _slugify(name_vi)

            price_low, price_high = category["price_range"]
            duration_low, duration_high = category["duration_range"]
            price = _money(rng, price_low, price_high)

            tags = list(category["tags"])
            if price == 0:
                tags.append("free")
            if price <= 50000:
                tags.append("budget")
            if price >= 150000:
                tags.append("premium")
            if rng.random() < 0.34:
                tags.append("wheelchair_accessible")
            if rng.random() < 0.30:
                tags.append("elderly_friendly")
            if rng.random() < 0.26:
                tags.append("quiet")
            tags = sorted(set(tags))

            media: list[dict[str, Any]] = []
            for media_index in range(rng.randint(1, 3)):
                filename = f"{venue_id}_{media_index + 1}.svg"
                relative = f"datasets/demo/media/{filename}"
                media_files[filename] = _svg(
                    name_vi,
                    f"{category['vi']} · {destination['vi']} (dữ liệu hư cấu)",
                    CATEGORY_PALETTE[category["id"]],
                    variant=media_index,
                )
                article = "an" if category["en"][0].lower() in "aeiou" else "a"
                media.append(_media_record(
                    media_id=f"media_{venue_id}_{media_index + 1}",
                    uri=relative,
                    alt_vi=f"Ảnh minh họa {name_vi}, {category['vi'].lower()} tại {destination['vi']}",
                    alt_en=(
                        f"Illustration of {name_vi}, {article} "
                        f"{category['en'].lower()} in {destination['en']}"
                    ),
                    role="cover" if media_index == 0 else "gallery",
                ))

            reviews = _reviews(rng, venue_id, rng.randint(2, 5))

            venues.append({
                "id": venue_id,
                "kind": "venue",
                "slug": slug,
                "name": {
                    "vi": name_vi,
                    "en": f"{name_vi} ({category['en']})",
                    "zh-Hans": f"{name_vi}（{category['zh']}）",
                },
                "destination_slug": destination["slug"],
                "category": category["id"],
                "tags": tags,
                "indoor": category["indoor"],
                "weather_fit": list(category["weather_fit"]),
                "hours": _hours(rng, category),
                "price": {"amount": price, "currency": "VND", "per": "person"},
                "duration_minutes": rng.randint(duration_low, duration_high),
                "coordinates": {
                    "lat": round(destination["lat"] + rng.uniform(-0.09, 0.09), 5),
                    "lon": round(destination["lon"] + rng.uniform(-0.09, 0.09), 5),
                },
                "media": media,
                "reviews": reviews,
                "rating": _rating_summary(reviews),
                "provenance": {
                    "source": "synthetic-generator",
                    "dataset_id": DATASET_ID,
                    "authority": "non-authoritative",
                },
            })

    for destination in DESTINATIONS:
        hotel_id = f"hotel_{destination['slug'].replace('-', '_')}"
        for service_category in SERVICE_CATEGORIES:
            service_id = f"service_{destination['slug'].replace('-', '_')}_{service_category['code']}"
            price_low, price_high = service_category["price"]
            minutes_low, minutes_high = service_category["minutes"]

            filename = f"{service_id}.svg"
            media_files[filename] = _svg(
                service_category["vi"],
                f"Dịch vụ tại {destination['vi']} (dữ liệu hư cấu)",
                CATEGORY_PALETTE["service"],
            )
            media = [_media_record(
                media_id=f"media_{service_id}_1",
                uri=f"datasets/demo/media/{filename}",
                alt_vi=f"Ảnh minh họa dịch vụ {service_category['vi'].lower()} tại {destination['vi']}",
                alt_en=f"Illustration of the {service_category['en'].lower()} service in {destination['en']}",
                role="cover",
            )]
            reviews = _reviews(rng, service_id, rng.randint(2, 4))

            services.append({
                "id": service_id,
                "kind": "service",
                "hotel_id": hotel_id,
                "destination_slug": destination["slug"],
                "code": service_category["code"],
                "name": {
                    "vi": service_category["vi"],
                    "en": service_category["en"],
                    "zh-Hans": service_category["zh"],
                },
                "category": "hotel_service",
                "tags": sorted(set(service_category["tags"])),
                "price": {
                    "amount": _money(rng, price_low, price_high),
                    "currency": "VND",
                    "per": "request",
                },
                "estimated_minutes": (
                    rng.randint(minutes_low, minutes_high) if minutes_high > 0 else None
                ),
                "requires_confirmation": True,
                "media": media,
                "reviews": reviews,
                "rating": _rating_summary(reviews),
                "provenance": {
                    "source": "synthetic-generator",
                    "dataset_id": DATASET_ID,
                    "authority": "non-authoritative",
                },
            })

    catalogue = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "provenance": "demo",
        "status": "synthetic",
        "authority": "non-authoritative",
        "generator": {
            "script": "scripts/generate_demo_catalogue.py",
            "seed": seed,
            "venues_per_destination": venues_per_destination,
        },
        "scope_note": (
            "Mọi tên, địa chỉ, giá, giờ mở cửa, ảnh và đánh giá trong tệp này đều "
            "là dữ liệu hư cấu phục vụ kiểm thử. Không dùng làm bằng chứng vận hành."
        ),
        "destinations": [
            {
                "slug": item["slug"],
                "name": {"vi": item["vi"], "en": item["en"], "zh-Hans": item["zh"]},
                "province_name": item["province"],
                "coordinates": {"lat": item["lat"], "lon": item["lon"]},
            }
            for item in DESTINATIONS
        ],
        "venues": venues,
        "services": services,
    }
    return catalogue, media_files


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Ghi bằng "\n" và sắp xếp khóa để kết quả giống nhau trên mọi hệ điều hành.
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--venues-per-destination", type=int, default=30)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Chỉ kiểm tra tệp trên đĩa có khớp kết quả sinh lại hay không, không ghi.",
    )
    arguments = parser.parse_args()

    catalogue, media_files = build_catalogue(
        arguments.seed, arguments.venues_per_destination
    )
    catalogue_path = DEMO_ROOT / "catalogue.json"
    expected_text = json.dumps(catalogue, ensure_ascii=False, indent=2) + "\n"

    if arguments.check:
        if not catalogue_path.exists():
            print(f"THIẾU: {catalogue_path}")
            return 1
        actual = catalogue_path.read_text(encoding="utf-8")
        if actual != expected_text:
            print(
                "LỆCH: datasets/demo/catalogue.json không khớp kết quả sinh lại.\n"
                "Chạy `python scripts/generate_demo_catalogue.py` rồi commit lại."
            )
            return 1
        missing = [
            name for name in media_files
            if not (MEDIA_ROOT / name).exists()
        ]
        if missing:
            print(f"THIẾU {len(missing)} tệp ảnh, ví dụ: {missing[:3]}")
            return 1
        print(
            f"Khớp: {len(catalogue['venues'])} địa điểm, "
            f"{len(catalogue['services'])} dịch vụ, {len(media_files)} ảnh."
        )
        return 0

    _write_json(catalogue_path, catalogue)
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    for name, content in media_files.items():
        (MEDIA_ROOT / name).write_text(content, encoding="utf-8", newline="\n")

    review_count = sum(len(item["reviews"]) for item in catalogue["venues"])
    review_count += sum(len(item["reviews"]) for item in catalogue["services"])
    print(
        f"Đã sinh {len(catalogue['venues'])} địa điểm, "
        f"{len(catalogue['services'])} dịch vụ, "
        f"{review_count} đánh giá, {len(media_files)} ảnh.\n"
        f"Catalogue: {catalogue_path.relative_to(PROJECT_ROOT)}\n"
        f"Ảnh:       {MEDIA_ROOT.relative_to(PROJECT_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
