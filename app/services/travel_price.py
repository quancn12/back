"""Honest replies to Travel price questions (roadmap C1).

There is no official tour price list yet: nothing with a responsible operator
and an effective date. Until one exists, a price question gets a fixed answer
that says so and points to the tour operator, instead of letting the model
guess a figure. The synthetic catalogue has prices, but they are demo data and
must never be quoted as tour prices.

When a real price list arrives, give it an ingest path with its owner and
effective date and quote it the way ``travel_policy`` quotes tour terms.
"""
from __future__ import annotations

import re

from app.services.travel_vector_store import normalize_search_text

# Matched on accent-folded text, so "giá", "gia" and "GIÁ" are the same. Each
# pattern needs a price word next to its object: a bare "gia" also means family
# ("gia đình") or value ("giá trị").
_PRICE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bgia (tour|ve|phong|tien|ca|combo|tron goi|tre em|nguoi lon|chuong trinh|uu dai|khuyen mai|hvnt|hvtb)\b",
        r"\bgia (tour |ve |phong |ca |tien |combo )?(khoang |la |tam |chung |moi |hien )?bao nhieu\b",
        r"\bgia cua (tour|chuong trinh|hvnt|hvtb)\b",
        r"\b(bang|bao) gia\b",
        r"\bchi phi\b",
        r"\bbao nhieu (tien|dong|vnd|usd)\b",
        r"\b(het|mat|ton|tong cong) (khoang )?bao nhieu\b",
        r"\b(price|prices|pricing|priced|cost|costs|fare|fares|quote)\b",
        r"\bhow much\b(?! (time|longer|walking|hiking|luggage))",
    )
)
_CJK_PRICE_MARKERS = ("价格", "多少钱", "费用", "报价", "价钱", "团费", "票价", "收费")


def is_price_query(message: str) -> bool:
    normalized = normalize_search_text(message)
    compact = re.sub(r"\s+", "", message)
    return any(pattern.search(normalized) for pattern in _PRICE_PATTERNS) or any(
        marker in compact for marker in _CJK_PRICE_MARKERS
    )


def price_reply(locale: str = "vi") -> str:
    return {
        "en": (
            "I don't have an **official tour price list** (one with a responsible operator "
            "and an effective date), so I won't quote a figure that could be wrong.\n\n"
            "Tour prices usually depend on the departure date, group size, room type and "
            "included services. Please contact the tour operator directly for a current quote.\n\n"
            "I can still help with itineraries, destinations and each tour's cancellation "
            "or change terms."
        ),
        "zh-Hans": (
            "我目前没有**正式的行程价格表**（需有负责单位和生效日期），为避免报错，不提供具体数字。\n\n"
            "行程价格通常取决于出发日期、人数、房型和包含的服务。请直接联系旅行社获取最新报价。\n\n"
            "我仍可以帮你了解行程安排、目的地以及各行程的取消或改期条款。"
        ),
    }.get(
        locale,
        "Mình chưa có **bảng giá tour chính thức** (có đơn vị chịu trách nhiệm và ngày áp dụng), "
        "nên không đưa con số để tránh báo sai.\n\n"
        "Giá tour thường thay đổi theo ngày khởi hành, số khách, hạng phòng và dịch vụ đi kèm. "
        "Bạn vui lòng liên hệ trực tiếp đơn vị tổ chức tour để nhận báo giá mới nhất.\n\n"
        "Mình vẫn giúp được bạn về lịch trình, điểm đến và điều khoản hủy/đổi của từng tour.",
    )
