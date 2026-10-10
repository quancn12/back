"""Generate the RAG-ready PDF from the canonical synthetic demo dataset."""
from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = PROJECT_ROOT / "demo_data" / "vietsage_demo_source.json"
OUTPUT_PATH = (
    PROJECT_ROOT
    / "output"
    / "pdf"
    / "VSDEMO 0001-26.pdf"
)


def _register_fonts() -> tuple[str, str]:
    candidates = [
        (
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
        ),
        (
            Path("C:/Windows/Fonts/DejaVuSans.ttf"),
            Path("C:/Windows/Fonts/DejaVuSans-Bold.ttf"),
        ),
    ]
    for regular, bold in candidates:
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("VietSage", str(regular)))
            pdfmetrics.registerFont(TTFont("VietSage-Bold", str(bold)))
            return "VietSage", "VietSage-Bold"
    return "Helvetica", "Helvetica-Bold"


def _money(value: int) -> str:
    return f"{value:,} VND".replace(",", ".")


def main() -> int:
    with SOURCE_PATH.open("r", encoding="utf-8") as stream:
        data = json.load(stream)
    hotel = data["hotel"]
    regular, bold = _register_fonts()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "TitleDemo",
        parent=styles["Title"],
        fontName=bold,
        fontSize=23,
        leading=28,
        textColor=colors.HexColor("#174A3A"),
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    subtitle = ParagraphStyle(
        "SubtitleDemo",
        parent=styles["Normal"],
        fontName=regular,
        fontSize=11,
        leading=16,
        textColor=colors.HexColor("#5B655F"),
        alignment=TA_CENTER,
        spaceAfter=14,
    )
    heading = ParagraphStyle(
        "HeadingDemo",
        parent=styles["Heading2"],
        fontName=bold,
        fontSize=15,
        leading=19,
        textColor=colors.HexColor("#174A3A"),
        spaceBefore=12,
        spaceAfter=7,
    )
    body = ParagraphStyle(
        "BodyDemo",
        parent=styles["BodyText"],
        fontName=regular,
        fontSize=10.2,
        leading=15,
        textColor=colors.HexColor("#202923"),
        spaceAfter=5,
    )
    note = ParagraphStyle(
        "NoteDemo",
        parent=body,
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#7A3E00"),
        backColor=colors.HexColor("#FFF4E5"),
        borderPadding=8,
        spaceAfter=12,
    )
    cell = ParagraphStyle("CellDemo", parent=body, fontSize=8.6, leading=11)
    cell_bold = ParagraphStyle("CellBoldDemo", parent=cell, fontName=bold)

    doc = SimpleDocTemplate(
        str(OUTPUT_PATH),
        pagesize=A4,
        rightMargin=17 * mm,
        leftMargin=17 * mm,
        topMargin=17 * mm,
        bottomMargin=16 * mm,
        title="VietSage Demo Hotel and Travel Guide",
        author="VietSage synthetic dataset generator",
    )

    def footer(canvas, current_doc):
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(colors.HexColor("#6B746E"))
        canvas.drawString(17 * mm, 9 * mm, "VietSage - Dữ liệu giả lập")
        canvas.drawRightString(193 * mm, 9 * mm, f"Trang {current_doc.page}")
        canvas.restoreState()

    story = [
        Spacer(1, 15 * mm),
        Paragraph("VIETSAGE DEMO", title),
        Paragraph("Hướng dẫn khách sạn và trải nghiệm Đà Nẵng giả lập", subtitle),
        Paragraph(
            "TÀI LIỆU GIẢ LẬP. Tất cả tên, địa chỉ, giá, lịch và dịch vụ có nhãn DEMO "
            "chỉ dùng để kiểm thử sản phẩm, không được dùng để tư vấn vận hành thật.",
            note,
        ),
        Paragraph(f"Khách sạn: {hotel['name']}", body),
        Paragraph(f"Địa chỉ giả lập: {hotel['address']}", body),
        Paragraph(f"Check-in: {hotel['check_in_time']} | Checkout: {hotel['check_out_time']}", body),
        Paragraph("1. THÔNG TIN LƯU TRÚ", heading),
    ]

    for item in hotel["public_information"]:
        story.append(Paragraph(f"- {item}", body))
    story.extend(
        [
            Paragraph("Thông tin Wi-Fi", heading),
            Paragraph(
                f"Tên mạng: {hotel['wifi']['ssid']}. Mật khẩu: {hotel['wifi']['password']}.",
                body,
            ),
            Paragraph("Liên hệ nội bộ", heading),
            Paragraph(
                f"Lễ tân: số {hotel['contacts']['reception_extension']}. "
                f"Room service: số {hotel['contacts']['room_service_extension']}. "
                f"Hỗ trợ khẩn cấp: số {hotel['contacts']['emergency_extension']}.",
                body,
            ),
            Paragraph("2. DỊCH VỤ KHÁCH SẠN", heading),
        ]
    )

    service_rows = [
        [Paragraph("Dịch vụ", cell_bold), Paragraph("Giá demo", cell_bold), Paragraph("Thời gian", cell_bold), Paragraph("Dự kiến", cell_bold)]
    ]
    for item in data["service_catalog"]:
        service_rows.append(
            [
                Paragraph(item["name"], cell),
                Paragraph("Miễn phí" if item["price"] == 0 else _money(item["price"]), cell),
                Paragraph(item["hours"], cell),
                Paragraph(f"{item['estimated_minutes']} phút", cell),
            ]
        )
    service_table = Table(service_rows, colWidths=[55 * mm, 30 * mm, 55 * mm, 25 * mm], repeatRows=1)
    service_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDEEE8")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#A9BBB4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend(
        [
            service_table,
            Paragraph(
                "Mỗi dịch vụ phải được khách xác nhận trước khi tạo yêu cầu. "
                "Xe sân bay cần đặt trước tối thiểu 60 phút.",
                note,
            ),
            PageBreak(),
            Paragraph("3. THỰC ĐƠN ROOM SERVICE", heading),
        ]
    )
    for item in data["menu"]:
        allergens = ", ".join(item["allergens"]) or "không ghi nhận"
        tags = ", ".join(item["dietary_tags"]) or "không có"
        story.append(
            Paragraph(
                f"<b>{item['name']}</b> - {_money(item['price'])}. "
                f"Phục vụ {item['available']}. Nhãn ăn kiêng: {tags}. "
                f"Chất gây dị ứng được ghi nhận: {allergens}.",
                body,
            )
        )

    story.append(Paragraph("4. ĐIỂM ĐẾN VÀ GỢI Ý GẦN KHÁCH SẠN", heading))
    for place in data["places"]:
        environment = "trong nhà" if place["indoor"] else "ngoài trời"
        child = "phù hợp trẻ em" if place["child_friendly"] else "không đánh dấu phù hợp trẻ em"
        story.append(
            Paragraph(
                f"<b>{place['name']}</b>: cách khách sạn {place['distance_from_hotel_km']} km, "
                f"{environment}, {child}, thời lượng tham khảo {place['recommended_duration_minutes']} phút. "
                f"{place['notes']}",
                body,
            )
        )

    story.extend([Paragraph("5. CHÍNH SÁCH KHÁCH SẠN", heading)])
    for item in hotel["policies"]:
        story.append(Paragraph(f"- {item}", body))

    story.extend(
        [
            Paragraph("6. QUY TẮC GỢI Ý CHỦ ĐỘNG TRONG BẢN DEMO", heading),
            Paragraph(
                "Sau check-in, ứng dụng có thể hiển thị hướng dẫn Wi-Fi, bữa sáng và tiện ích. "
                "Thông tin công khai vẫn được hiển thị khi khách từ chối cá nhân hóa.",
                body,
            ),
            Paragraph(
                "Khi mưa lớn, ưu tiên địa điểm trong nhà và loại gợi ý Biển Mỹ Khê hoặc Bán đảo Sơn Trà. "
                "Khi sắp checkout mà chưa có phương tiện, có thể tạo bản nháp xe sân bay nhưng không được "
                "tạo yêu cầu tính phí trước khi khách xác nhận.",
                body,
            ),
            Paragraph(
                "Khung giờ yên tĩnh là 22:00-07:00. Recommendation không khẩn cấp bị tạm dừng trong khung giờ này. "
                "Sau khi rút đồng ý cá nhân hóa, recommendation dựa trên hành vi hoặc sở thích suy luận phải dừng ngay.",
                body,
            ),
            Paragraph("7. CÂU HỎI KIỂM THỬ RAG", heading),
        ]
    )
    question_lines = [
        f"{index}. {item['question']}"
        for index, item in enumerate(data["rag_test_questions"], 1)
    ]
    story.append(Paragraph("<br/>".join(question_lines), body))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(f"Created {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
