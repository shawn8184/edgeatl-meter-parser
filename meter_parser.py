from flask import Flask, request, jsonify
from dataclasses import dataclass, asdict, field
from datetime import datetime
from typing import Optional, List
from html import unescape
import re

app = Flask(__name__)

@dataclass
class ParsedMeter:
    equipment_number: Optional[str] = None
    serial_number: Optional[str] = None
    model_number: Optional[str] = None
    meter_black: Optional[int] = None
    meter_color: Optional[int] = None
    reading_date: Optional[str] = None
    # Added field to capture the full cleaned email content for later review
    raw_text: Optional[str] = None
    confidence: float = 0.0
    notes: List[str] = field(default_factory=list)

def clean_text(text: str) -> str:
    if not text:
        return ""
    text = unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>|</div>|</tr>|</li>|</table>|</h\d>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()

def try_parse_date(text: str) -> Optional[str]:
    patterns = [
        r"\b(\d{4}-\d{2}-\d{2})\b",
        r"\b(\d{1,2}/\d{1,2}/\d{4})\b",
        r"\b(\d{1,2}-\d{1,2}-\d{4})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            raw = match.group(1)
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
                try:
                    return datetime.strptime(raw, fmt).date().isoformat()
                except ValueError:
                    pass
    return datetime.now().date().isoformat()

def parse_int(value: str) -> Optional[int]:
    try:
        return int(value.replace(",", "").strip())
    except Exception:
        return None

def normalize_id(value: str) -> str:
    return value.strip().upper()

def extract_equipment_number(text: str) -> Optional[str]:
    patterns = [
        r"(?:equipment|equip|eq)\s*(?:number|no|#)?\s*[:\-]?\s*([A-Z0-9\-]+)",
        r"\b(EQ[0-9A-Z\-]+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return normalize_id(match.group(1))
    return None

def extract_serial_number(text: str) -> Optional[str]:
    patterns = [
        r"(?:serial number|serial|sn)\s*(?:number|no|#)?\s*[:\-]?\s*([A-Z0-9\-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return normalize_id(match.group(1))
    return None

def extract_model_number(text: str) -> Optional[str]:
    patterns = [
        r"(?:model)\s*(?:number|no|#)?\s*[:\-]?\s*([A-Z0-9\-_]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return normalize_id(match.group(1))
    return None

def extract_black_meter(text: str) -> Optional[int]:
    patterns = [
        r"(?:black total|mono total|bw total|b/w total)\s*[:\-]?\s*([0-9,]{1,12})",
        r"(?:black|mono|bw|b/w)\s*[:\-]?\s*([0-9,]{1,12})",
        r"(?:total impressions|mono impressions|black impressions)\s*[:\-]?\s*([0-9,]{1,12})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return parse_int(match.group(1))
    return None

def extract_color_meter(text: str) -> Optional[int]:
    patterns = [
        r"(?:color total|clr total)\s*[:\-]?\s*([0-9,]{1,12})",
        r"(?:color|clr)\s*[:\-]?\s*([0-9,]{1,12})",
        r"(?:color impressions)\s*[:\-]?\s*([0-9,]{1,12})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return parse_int(match.group(1))
    return None

def fallback_extract_numeric_values(text: str, result: ParsedMeter) -> None:
    standalone_numbers = re.findall(r"\b([0-9,]{3,12})\b", text)
    parsed_numbers = [parse_int(x) for x in standalone_numbers]
    parsed_numbers = [x for x in parsed_numbers if x is not None]

    if result.meter_black is None and result.meter_color is None:
        if len(parsed_numbers) == 1:
            result.meter_black = parsed_numbers[0]
            result.notes.append("Single numeric value found; treated as black meter.")
        elif len(parsed_numbers) == 2:
            result.meter_black = parsed_numbers[0]
            result.meter_color = parsed_numbers[1]
            result.notes.append("Two unlabeled numeric values found; treated as black/color.")

def calculate_confidence(from_email: str, result: ParsedMeter) -> float:
    score = 0.0
    if result.equipment_number:
        score += 0.40
    if result.serial_number:
        score += 0.30
    if result.meter_black is not None:
        score += 0.20
    if result.meter_color is not None:
        score += 0.20
    if result.reading_date:
        score += 0.05
    if from_email and "@" in from_email:
        score += 0.05
    if any("treated as" in note.lower() for note in result.notes):
        score -= 0.15
    return round(max(0.0, min(score, 1.0)), 2)

def extract_meter_data(from_email: str, subject: str, body: str) -> ParsedMeter:
    full_text = clean_text(f"{subject}\n{body}")
    result = ParsedMeter()
    # Store the full cleaned text so that raw messages are available when parsing fails
    result.raw_text = full_text
    result.reading_date = try_parse_date(full_text)
    result.equipment_number = extract_equipment_number(full_text)
    result.serial_number = extract_serial_number(full_text)
    result.model_number = extract_model_number(full_text)
    result.meter_black = extract_black_meter(full_text)
    result.meter_color = extract_color_meter(full_text)
    fallback_extract_numeric_values(full_text, result)

    if not result.equipment_number and not result.serial_number:
        result.notes.append("No equipment number or serial number found.")
    if result.meter_black is None and result.meter_color is None:
        result.notes.append("No meter values found.")

    result.confidence = calculate_confidence(from_email, result)
    return result

@app.route("/parse-meter-email", methods=["POST"])
def parse_meter_email():
    data = request.get_json(silent=True) or {}
    from_email = data.get("from", "") or ""
    subject = data.get("subject", "") or ""
    body = data.get("body", "") or ""

    parsed = extract_meter_data(from_email=from_email, subject=subject, body=body)

    return jsonify({
        "success": True,
        "from": from_email,
        "parsed": asdict(parsed)
    }), 200

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)
