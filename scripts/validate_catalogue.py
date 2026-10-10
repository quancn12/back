"""Kiểm tra một catalogue địa điểm/dịch vụ theo hợp đồng dùng chung.

Công cụ này chạy trên cả dữ liệu demo lẫn dữ liệu thật, với **cùng một bộ luật**.
Đó là điều kiện để câu "học tốt trên demo thì thay bằng dữ liệu thật vẫn tốt"
trở thành một mệnh đề kiểm chứng được thay vì một lời hứa.

Ba nhóm kiểm tra:

1. **Cấu trúc** — mọi bản ghi có đủ trường bắt buộc, đúng kiểu, ID không trùng,
   ảnh tham chiếu tới tệp có thật.
2. **Giấy phép** — mỗi ảnh và mỗi đánh giá phải khai báo rõ được lưu hay không,
   được phát hành lại hay không, được đưa vào dữ liệu huấn luyện hay không.
   Thiếu khai báo là lỗi, không phải mặc định cho phép.
3. **Khả năng trả lời** — mọi câu hỏi vàng phải tìm được đủ số kết quả tối thiểu
   trong catalogue. Đây là phép thử hoán đổi: nếu thay dữ liệu demo bằng dữ liệu
   thật mà một câu hỏi không còn trả lời được, nghĩa là dữ liệu thật thiếu
   thuộc tính nào đó, và công cụ chỉ ra ngay thuộc tính nào.

Với `--compare`, công cụ chạy cùng bộ câu hỏi vàng trên hai catalogue và báo
cáo chênh lệch. Đây là cổng chặn trước khi tuyên bố dữ liệu thật đã sẵn sàng.

Chỉ dùng thư viện chuẩn, không thêm phụ thuộc vào `requirements.txt`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN = PROJECT_ROOT / "datasets" / "golden" / "queries.json"

LICENSE_FLAGS = ("can_store", "can_redistribute", "can_train")
REQUIRED_VENUE_FIELDS = (
    "id", "kind", "name", "destination_slug", "category", "tags", "indoor",
    "weather_fit", "hours", "price", "duration_minutes", "coordinates",
    "media", "reviews", "rating", "provenance",
)
REQUIRED_SERVICE_FIELDS = (
    "id", "kind", "hotel_id", "code", "name", "category", "tags", "price",
    "media", "reviews", "rating", "provenance",
)


class Problems:
    """Gom lỗi lại để báo cáo một lần, thay vì dừng ở lỗi đầu tiên."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _get_path(record: dict, dotted: str) -> Any:
    """Đọc giá trị theo đường dẫn kiểu ``price.amount``."""
    value: Any = record
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _check_license(block: Any, where: str, problems: Problems) -> None:
    if not isinstance(block, dict):
        problems.error(f"{where}: thiếu khối 'license'.")
        return
    if not block.get("type"):
        problems.error(f"{where}: 'license.type' trống.")
    for flag in LICENSE_FLAGS:
        if flag not in block:
            # Thiếu cờ không được coi là cho phép. Một ảnh không khai báo
            # can_train mà lọt vào tập huấn luyện là đúng loại sự cố mà
            # kiến trúc này sinh ra để ngăn.
            problems.error(f"{where}: thiếu cờ giấy phép '{flag}'. Không suy diễn mặc định.")
        elif not isinstance(block[flag], bool):
            problems.error(f"{where}: cờ '{flag}' phải là true/false.")
    if block.get("can_redistribute") and not block.get("can_store"):
        problems.warn(
            f"{where}: cho phát hành lại nhưng không cho lưu trữ. Tổ hợp này hiếm, "
            "hãy kiểm tra lại điều khoản nhà cung cấp."
        )
    if block.get("can_train") and not block.get("can_store"):
        problems.error(
            f"{where}: cho huấn luyện nhưng không cho lưu trữ. Không thể huấn luyện "
            "trên dữ liệu không được phép giữ."
        )


def _check_media(media: Any, where: str, root: Path, problems: Problems) -> None:
    if not isinstance(media, list):
        problems.error(f"{where}: 'media' phải là danh sách.")
        return
    for index, item in enumerate(media):
        label = f"{where}.media[{index}]"
        if not isinstance(item, dict):
            problems.error(f"{label}: phải là đối tượng.")
            continue
        for field in ("id", "kind", "uri", "alt", "license", "provenance"):
            if field not in item:
                problems.error(f"{label}: thiếu trường '{field}'.")
        _check_license(item.get("license"), label, problems)
        uri = item.get("uri")
        if isinstance(uri, str) and not uri.startswith(("http://", "https://")):
            # Ảnh cục bộ phải tồn tại. Ảnh từ xa không kiểm tra ở đây vì việc đó
            # cần gọi mạng, và công cụ này phải chạy được khi không có mạng.
            if not (root / uri).exists():
                problems.error(f"{label}: không tìm thấy tệp ảnh '{uri}'.")
        alt = item.get("alt")
        if not isinstance(alt, dict) or not alt.get("vi"):
            problems.error(f"{label}: thiếu mô tả thay thế (alt) tiếng Việt.")


def _check_reviews(reviews: Any, where: str, problems: Problems) -> None:
    if not isinstance(reviews, list):
        problems.error(f"{where}: 'reviews' phải là danh sách.")
        return
    for index, item in enumerate(reviews):
        label = f"{where}.reviews[{index}]"
        if not isinstance(item, dict):
            problems.error(f"{label}: phải là đối tượng.")
            continue
        rating = item.get("rating")
        if not isinstance(rating, int) or not 1 <= rating <= 5:
            problems.error(f"{label}: 'rating' phải là số nguyên từ 1 đến 5.")
        if not item.get("text"):
            problems.error(f"{label}: 'text' trống.")
        _check_license(item.get("license"), label, problems)


def _check_rating_summary(record: dict, where: str, problems: Problems) -> None:
    summary = record.get("rating")
    reviews = record.get("reviews") or []
    if not isinstance(summary, dict):
        problems.error(f"{where}: thiếu khối 'rating'.")
        return
    if summary.get("count") != len(reviews):
        problems.error(
            f"{where}: rating.count = {summary.get('count')} nhưng có "
            f"{len(reviews)} đánh giá. Số liệu tổng hợp phải khớp dữ liệu gốc."
        )
    if reviews:
        scores = [r.get("rating") for r in reviews if isinstance(r.get("rating"), int)]
        if scores:
            expected = round(sum(scores) / len(scores), 2)
            actual = summary.get("average")
            if not isinstance(actual, (int, float)) or abs(actual - expected) > 0.011:
                problems.error(
                    f"{where}: rating.average = {actual} nhưng tính lại được {expected}."
                )


def validate_structure(catalogue: dict, root: Path, problems: Problems) -> None:
    for field in ("schema_version", "dataset_id", "provenance", "venues", "services"):
        if field not in catalogue:
            problems.error(f"catalogue: thiếu trường gốc '{field}'.")

    destination_slugs = {
        item.get("slug") for item in catalogue.get("destinations", []) if isinstance(item, dict)
    }
    seen_ids: set[str] = set()

    for kind, required in (("venues", REQUIRED_VENUE_FIELDS), ("services", REQUIRED_SERVICE_FIELDS)):
        records = catalogue.get(kind)
        if not isinstance(records, list):
            problems.error(f"catalogue.{kind}: phải là danh sách.")
            continue
        if not records:
            problems.warn(f"catalogue.{kind}: danh sách rỗng.")
        for record in records:
            if not isinstance(record, dict):
                problems.error(f"catalogue.{kind}: phần tử không phải đối tượng.")
                continue
            record_id = record.get("id") or "<thiếu id>"
            where = f"{kind}[{record_id}]"
            for field in required:
                if field not in record:
                    problems.error(f"{where}: thiếu trường '{field}'.")
            if record_id in seen_ids:
                problems.error(f"{where}: ID bị trùng.")
            seen_ids.add(record_id)

            slug = record.get("destination_slug")
            if slug is not None and destination_slugs and slug not in destination_slugs:
                problems.error(f"{where}: destination_slug '{slug}' không có trong danh sách điểm đến.")

            price = record.get("price")
            if isinstance(price, dict):
                amount = price.get("amount")
                if not isinstance(amount, int) or amount < 0:
                    problems.error(f"{where}: 'price.amount' phải là số nguyên không âm.")
                if not price.get("currency"):
                    problems.error(f"{where}: 'price.currency' trống.")

            tags = record.get("tags")
            if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                problems.error(f"{where}: 'tags' phải là danh sách chuỗi.")

            _check_media(record.get("media"), where, root, problems)
            _check_reviews(record.get("reviews"), where, problems)
            _check_rating_summary(record, where, problems)


# ---------------------------------------------------------------------------
# Đánh giá điều kiện của câu hỏi vàng
# ---------------------------------------------------------------------------

def _evaluate(predicate: dict, record: dict) -> bool:
    field = predicate.get("field", "")
    operator = predicate.get("op", "")
    expected = predicate.get("value")
    actual = _get_path(record, field)

    if operator == "eq":
        return actual == expected
    if operator == "ne":
        return actual != expected
    if operator == "lte":
        return isinstance(actual, (int, float)) and actual <= expected
    if operator == "gte":
        return isinstance(actual, (int, float)) and actual >= expected
    if operator == "contains_any":
        return isinstance(actual, list) and any(value in actual for value in expected)
    if operator == "contains_all":
        return isinstance(actual, list) and all(value in actual for value in expected)
    if operator == "excludes_any":
        return isinstance(actual, list) and not any(value in actual for value in expected)
    raise ValueError(f"Toán tử không được hỗ trợ: {operator!r}")


def _matches(case: dict, record: dict) -> bool:
    for predicate in case.get("must_satisfy", []):
        if not _evaluate(predicate, record):
            return False
    for predicate in case.get("must_not_satisfy", []):
        # must_not_satisfy nghĩa là bản ghi KHÔNG được thỏa điều kiện này.
        if _evaluate(predicate, record):
            return False
    return True


def run_golden(catalogue: dict, golden: dict) -> list[dict[str, Any]]:
    venues = [item for item in catalogue.get("venues", []) if isinstance(item, dict)]
    results: list[dict[str, Any]] = []
    for case in golden.get("cases", []):
        matched = [venue for venue in venues if _matches(case, venue)]
        minimum = case.get("min_results", 1)
        results.append({
            "id": case.get("id"),
            "question": case.get("question"),
            "matched": len(matched),
            "min_results": minimum,
            "answerable": len(matched) >= minimum,
            "coverage": round(len(matched) / len(venues), 4) if venues else 0.0,
        })
    return results


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _report_golden(label: str, results: list[dict[str, Any]]) -> int:
    failures = [row for row in results if not row["answerable"]]
    print(f"\nCâu hỏi vàng trên {label}: {len(results) - len(failures)}/{len(results)} trả lời được.")
    for row in failures:
        print(
            f"  KHÔNG TRẢ LỜI ĐƯỢC  {row['id']}: tìm được {row['matched']} kết quả, "
            f"cần tối thiểu {row['min_results']}"
        )
        print(f"      câu hỏi: {row['question']}")
    return len(failures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalogue", help="Đường dẫn tới tệp catalogue JSON cần kiểm tra.")
    parser.add_argument(
        "--golden",
        default=str(DEFAULT_GOLDEN),
        help="Bộ câu hỏi vàng. Mặc định: datasets/golden/queries.json",
    )
    parser.add_argument(
        "--compare",
        help="Catalogue thứ hai để so sánh. Dùng khi đối chiếu dữ liệu demo với dữ liệu thật.",
    )
    parser.add_argument(
        "--require-trainable",
        action="store_true",
        help="Báo lỗi nếu có bản ghi can_train=false. Dùng trước khi xuất dữ liệu huấn luyện.",
    )
    arguments = parser.parse_args()

    catalogue_path = Path(arguments.catalogue).resolve()
    if not catalogue_path.exists():
        print(f"Không tìm thấy catalogue: {catalogue_path}")
        return 1

    catalogue = _load(catalogue_path)
    problems = Problems()
    validate_structure(catalogue, PROJECT_ROOT, problems)

    if arguments.require_trainable:
        blocked = 0
        for kind in ("venues", "services"):
            for record in catalogue.get(kind, []):
                for group in ("media", "reviews"):
                    for item in record.get(group, []) or []:
                        if not (item.get("license") or {}).get("can_train", False):
                            blocked += 1
        if blocked:
            problems.error(
                f"Có {blocked} bản ghi ảnh/đánh giá có can_train=false. "
                "Không được đưa chúng vào tập huấn luyện."
            )

    venues = catalogue.get("venues", [])
    services = catalogue.get("services", [])
    media_count = sum(len(item.get("media") or []) for item in list(venues) + list(services))
    review_count = sum(len(item.get("reviews") or []) for item in list(venues) + list(services))
    print(
        f"Catalogue: {catalogue_path.name}\n"
        f"  dataset_id : {catalogue.get('dataset_id')}\n"
        f"  nguồn gốc  : {catalogue.get('provenance')} / {catalogue.get('status')}\n"
        f"  địa điểm   : {len(venues)}\n"
        f"  dịch vụ    : {len(services)}\n"
        f"  ảnh        : {media_count}\n"
        f"  đánh giá   : {review_count}"
    )

    for warning in problems.warnings:
        print(f"  CẢNH BÁO  {warning}")
    for error in problems.errors[:40]:
        print(f"  LỖI       {error}")
    if len(problems.errors) > 40:
        print(f"  ... và {len(problems.errors) - 40} lỗi nữa.")

    golden_path = Path(arguments.golden).resolve()
    if not golden_path.exists():
        print(f"Không tìm thấy bộ câu hỏi vàng: {golden_path}")
        return 1
    golden = _load(golden_path)

    results = run_golden(catalogue, golden)
    unanswerable = _report_golden(catalogue_path.name, results)

    exit_code = 1 if (problems.errors or unanswerable) else 0

    if arguments.compare:
        other_path = Path(arguments.compare).resolve()
        if not other_path.exists():
            print(f"Không tìm thấy catalogue so sánh: {other_path}")
            return 1
        other = _load(other_path)
        other_results = run_golden(other, golden)
        other_unanswerable = _report_golden(other_path.name, other_results)

        by_id = {row["id"]: row for row in results}
        print(f"\nSo sánh {catalogue_path.name} -> {other_path.name}:")
        regressions = 0
        for row in other_results:
            baseline = by_id.get(row["id"])
            if baseline is None:
                continue
            if baseline["answerable"] and not row["answerable"]:
                regressions += 1
                print(
                    f"  SUY GIẢM  {row['id']}: trả lời được trên bộ gốc "
                    f"({baseline['matched']} kết quả) nhưng không trên bộ mới "
                    f"({row['matched']} kết quả)."
                )
        if regressions:
            print(
                f"\n{regressions} câu hỏi bị suy giảm. Dữ liệu mới thiếu thuộc tính mà "
                "bộ gốc có. Không tuyên bố sẵn sàng cho tới khi xử lý xong."
            )
            exit_code = 1
        elif other_unanswerable == 0:
            print("  Không có suy giảm. Bộ dữ liệu mới trả lời được mọi câu hỏi vàng.")

    print("\nKẾT QUẢ: " + ("ĐẠT" if exit_code == 0 else "KHÔNG ĐẠT"))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
