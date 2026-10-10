"""Build the factual PDF consumed by the normal Travel ingestion reader."""
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
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "demo_facts.json"
OUTPUT = ROOT / "corpus" / "demo_facts.pdf"


def _fonts() -> tuple[str, str]:
    for regular, bold in (
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
        (Path("C:/Windows/Fonts/DejaVuSans.ttf"), Path("C:/Windows/Fonts/DejaVuSans-Bold.ttf")),
    ):
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("DemoSans", str(regular)))
            pdfmetrics.registerFont(TTFont("DemoSans-Bold", str(bold)))
            return "DemoSans", "DemoSans-Bold"
    return "Helvetica", "Helvetica-Bold"


def _money(value: int) -> str:
    return f"{value:,} VND".replace(",", ".")


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(str(text).replace("&", "&amp;"), style)


def main() -> int:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    regular, bold = _fonts()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    title = ParagraphStyle("DemoTitle", parent=styles["Title"], fontName=bold, fontSize=20, leading=25, alignment=TA_CENTER, textColor=colors.HexColor("#174A3A"), spaceAfter=8)
    subtitle = ParagraphStyle("DemoSubtitle", parent=styles["Normal"], fontName=regular, fontSize=10, leading=14, alignment=TA_CENTER, textColor=colors.HexColor("#5B655F"), spaceAfter=12)
    heading = ParagraphStyle("DemoHeading", parent=styles["Heading2"], fontName=bold, fontSize=14, leading=18, textColor=colors.HexColor("#174A3A"), spaceBefore=10, spaceAfter=6)
    body = ParagraphStyle("DemoBody", parent=styles["BodyText"], fontName=regular, fontSize=9.2, leading=13.5, textColor=colors.HexColor("#202923"), spaceAfter=4)
    note = ParagraphStyle("DemoNote", parent=body, fontSize=8.8, leading=12.5, textColor=colors.HexColor("#7A3E00"), backColor=colors.HexColor("#FFF4E5"), borderPadding=7, spaceAfter=10)
    cell = ParagraphStyle("DemoCell", parent=body, fontSize=7.8, leading=10)
    cell_bold = ParagraphStyle("DemoCellBold", parent=cell, fontName=bold)

    doc = SimpleDocTemplate(str(OUTPUT), pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm, topMargin=15 * mm, bottomMargin=15 * mm, title="VietSage synthetic demo facts", author="VietSage local fixture")

    def footer(canvas, current_doc):
        canvas.saveState()
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(colors.HexColor("#6B746E"))
        canvas.drawString(16 * mm, 8 * mm, "DEMO DATA - SYNTHETIC - NON-AUTHORITATIVE")
        canvas.drawRightString(194 * mm, 8 * mm, f"Page {current_doc.page}")
        canvas.restoreState()

    story: list[object] = [
        Spacer(1, 8 * mm),
        _p("VIETSAGE DEMO FACTS", title),
        _p("Fictional records for local RAG and reading workflow checks", subtitle),
        _p("All names, prices, hours, policies, schedules, and constraints in this document are synthetic test facts. They do not describe a real hotel or service and contain no map coordinates, reviews, photos, or booking authority.", note),
        _p(f"Dataset: {data['dataset_id']} | Provenance: {data['provenance']} | Status: {data['status']} | Authority: {data['authority']}", body),
        _p("1. FICTIONAL HOTEL FACTS", heading),
        _p(f"Name: {data['hotel']['name']}. Address label: {data['hotel']['address']}. Check-in: {data['hotel']['check_in']}. Check-out: {data['hotel']['check_out']}.", body),
        _p("Amenities", heading),
    ]
    for item in data["hotel"]["amenities"]:
        story.append(_p(f"{item['name']}: {item['value']}", body))
    story.append(_p("Hours", heading))
    for item in data["hotel"]["hours"]:
        story.append(_p(f"{item['service']}: {item['hours']}", body))
    story.append(_p("Policies", heading))
    for item in data["hotel"]["policies"]:
        story.append(_p(item, body))

    story.extend([_p("2. FICTIONAL DISCOVERY RECORDS", heading), _p("Each row is an independent fact. A planner may select records according to requested duration, weather, budget, and constraints; this document does not prescribe a fixed itinerary.", body)])
    rows = [[_p("Place", cell_bold), _p("Type", cell_bold), _p("Hours", cell_bold), _p("Minutes", cell_bold), _p("Cost", cell_bold), _p("Setting", cell_bold), _p("Weather", cell_bold)]]
    for item in data["discovery_points"]:
        rows.append([_p(item["name"], cell), _p(item["type"], cell), _p(item["hours"], cell), _p(item["duration_minutes"], cell), _p(_money(item["price_vnd"]), cell), _p("indoor" if item["indoor"] else "outdoor", cell), _p(item["weather"], cell)])
    table = Table(rows, colWidths=[34 * mm, 25 * mm, 42 * mm, 18 * mm, 23 * mm, 22 * mm, 24 * mm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDEEE8")), ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#A9BBB4")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story.append(table)
    story.append(Spacer(1, 4))
    for item in data["discovery_points"]:
        story.append(_p(f"{item['name']} constraints: {item['constraints']}", body))
    story.append(_p("Transport facts", heading))
    for item in data["transport_facts"]:
        story.append(_p(f"{item['from']} to {item['to']}: {item['typical_minutes']} minutes; {item['mode']}.", body))

    story.extend([_p("3. FICTIONAL STUDY SCHEDULE", heading), _p("The following slots describe availability in a synthetic study setting. A missing or closed slot should be treated as unavailable rather than inferred.", body)])
    schedule = [[_p("Slot", cell_bold), _p("Activity", cell_bold), _p("Room", cell_bold), _p("Availability", cell_bold)]]
    for item in data["study_schedule"]:
        schedule.append([_p(item["slot"], cell), _p(item["activity"], cell), _p(item["room"], cell), _p(item["availability"], cell)])
    study_table = Table(schedule, colWidths=[35 * mm, 55 * mm, 45 * mm, 45 * mm], repeatRows=1)
    study_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9E2F5")), ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B3A5C9")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story.append(study_table)
    story.append(_p("Use these records to test fact extraction, grounded discovery, comparisons, budget arithmetic, variable-length planning, and fallback when a requested field is absent. The default corpus does not include the negative fixtures in data/demo/negativecases.", note))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
