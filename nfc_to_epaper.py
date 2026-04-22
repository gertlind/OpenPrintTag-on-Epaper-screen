from smartcard.System import readers
from smartcard.Exceptions import NoCardException
import requests
from io import BytesIO
import cbor2
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
import datetime

API_BASE = "http://localhost:3456/api"

OPENEPAPER_HOST = "192.168.123.90"
TAG_MAC = "00EE6933F1335F55"

GET_UID = [0xFF, 0xCA, 0x00, 0x00, 0x00]
MIME = b"application/vnd.openprinttag"

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_IMAGE = BASE_DIR / "current_tag.jpg"

WIDTH = 960
HEIGHT = 672

FONT_REG_PATHS = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Helvetica.ttc",
]
FONT_BOLD_PATHS = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Helvetica.ttc",
]


def get_font(paths, size):
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def hex_compact(data):
    return "".join(f"{b:02X}" for b in data)


def transmit_ok(conn, apdu, label):
    data, sw1, sw2 = conn.transmit(apdu)
    if (sw1, sw2) != (0x90, 0x00):
        raise RuntimeError(f"{label} failed: {sw1:02X} {sw2:02X}")
    return data


def read_multiple_blocks(conn, first_block: int, count: int):
    apdu = [
        0xFF, 0xFB, 0x00, 0x00, 0x03,
        0x23,
        first_block & 0xFF,
        (count - 1) & 0xFF
    ]
    return transmit_ok(conn, apdu, f"Read Multiple Blocks {first_block}+{count}")


def find_openprinttag_payload(raw: bytes) -> bytes:
    mime_pos = raw.find(MIME)
    if mime_pos == -1:
        raise ValueError("Could not find application/vnd.openprinttag")
    return raw[mime_pos + len(MIME):]


def decode_cbor_sequence(data: bytes):
    bio = BytesIO(data)
    decoder = cbor2.CBORDecoder(bio)
    objects = []

    while bio.tell() < len(data):
        try:
            obj = decoder.decode()
            objects.append(obj)
        except EOFError:
            break
        except Exception:
            break

    return objects


def extract_openprinttag_fields(objects):
    for obj in objects:
        if isinstance(obj, dict) and 10 in obj and 11 in obj:
            return {
                "material_class": obj.get(8),
                "material_type_code": obj.get(9),
                "material_name": obj.get(10),
                "brand_name": obj.get(11),
                "nominal_netto_full_weight": obj.get(16),
                "actual_netto_full_weight": obj.get(17),
                "empty_container_weight": obj.get(18),
                "rgb_bytes": obj.get(19),
                "density": obj.get(29),
                "min_print_temperature": obj.get(34),
                "max_print_temperature": obj.get(35),
                "preheat_temperature": obj.get(36),
                "min_bed_temperature": obj.get(37),
                "max_bed_temperature": obj.get(38),
                "finish_code": obj.get(41),
                "material_type_name": obj.get(52),
                "country": obj.get(55),
                "instance_id": obj.get(5),
            }

    raise ValueError("Could not find OpenPrintTag field map in CBOR objects")


def fetch_all_filaments():
    url = f"{API_BASE}/filaments"
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    data = r.json()

    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        if "items" in data and isinstance(data["items"], list):
            return data["items"]
        if "data" in data and isinstance(data["data"], list):
            return data["data"]

    raise ValueError("Unexpected response format from /filaments")


def find_filament_by_instance_id(instance_id: str):
    filaments = fetch_all_filaments()
    print(f"Fetched {len(filaments)} filaments from API")
    print("Looking for instanceId:", instance_id)

    for filament in filaments:
        if filament.get("instanceId") == instance_id:
            print("Matched filament:", filament.get("name"))
            return filament

    raise ValueError(f"No filament found with instanceId {instance_id}")


def rgb_hex_or_default(value):
    if isinstance(value, str) and value.startswith("#") and len(value) == 7:
        return value
    return "#000000"


def fit_text(draw, text, font_paths, max_width, start_size, min_size=18):
    size = start_size
    while size >= min_size:
        font = get_font(font_paths, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]
        if width <= max_width:
            return font
        size -= 1
    return get_font(font_paths, min_size)


def draw_metric_box(draw, x, y, w, h, title, value, title_font, value_font):
    draw.rounded_rectangle((x, y, x + w, y + h), radius=14, outline=180, width=2, fill=245)
    draw.text((x + 18, y + 14), title, font=title_font, fill=110)
    draw.text((x + 18, y + 52), value, font=value_font, fill=0)


def render_filament_image(filament, outfile: Path):
    img = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(img)

    vendor = filament.get("vendor", "")
    name = filament.get("name", "")
    filament_type = filament.get("type", "")
    color_hex = rgb_hex_or_default(filament.get("color"))
    density = filament.get("density")
    diameter = filament.get("diameter")

    temps = filament.get("temperatures", {}) or {}
    nozzle = temps.get("nozzle")
    bed = temps.get("bed")

    spool_weight = filament.get("spoolWeight")
    net_weight = filament.get("netFilamentWeight")
    total_weight = filament.get("totalWeight")
    instance_id = filament.get("instanceId", "-")

    weight_left = None
    percent_left = None

    if spool_weight is not None and total_weight is not None:
        weight_left = total_weight - spool_weight

    if weight_left is not None and net_weight:
        percent_left = (weight_left / net_weight) * 100

    # Approx enligt hur du vill följa databasen
    # 302 g -> 98.9 m
    length_left = None
    if weight_left is not None:
        grams_per_meter = 302 / 98.9
        length_left = round(weight_left / grams_per_meter, 1)

    font_vendor = fit_text(draw, vendor, FONT_BOLD_PATHS, 680, 76, 42)
    font_name = fit_text(draw, name, FONT_BOLD_PATHS, 680, 48, 26)
    font_chip = fit_text(draw, filament_type, FONT_BOLD_PATHS, 220, 34, 20)

    font_box_title = get_font(FONT_REG_PATHS, 23)
    font_box_value = get_font(FONT_BOLD_PATHS, 36)
    font_section = get_font(FONT_BOLD_PATHS, 28)
    font_footer = get_font(FONT_REG_PATHS, 20)

    # Header
    draw.text((40, 28), vendor, font=font_vendor, fill=0)
    draw.text((40, 112), name, font=font_name, fill=0)

    chip_x, chip_y, chip_w, chip_h = 40, 170, 180, 56
    draw.rounded_rectangle((chip_x, chip_y, chip_x + chip_w, chip_y + chip_h), radius=14, fill=0)
    chip_bbox = draw.textbbox((0, 0), filament_type, font=font_chip)
    chip_tw = chip_bbox[2] - chip_bbox[0]
    chip_th = chip_bbox[3] - chip_bbox[1]
    draw.text(
        (chip_x + (chip_w - chip_tw) // 2, chip_y + (chip_h - chip_th) // 2 - 3),
        filament_type,
        font=font_chip,
        fill=255
    )

    # Color swatch
    try:
        draw.rounded_rectangle((780, 42, 900, 162), radius=12, fill=color_hex, outline=0, width=2)
    except Exception:
        draw.rounded_rectangle((780, 42, 900, 162), radius=12, fill=255, outline=0, width=2)

    draw.line((40, 252, 920, 252), fill=170, width=2)

    # Top row
    draw_metric_box(draw, 40, 280, 200, 90, "Diameter", f"{diameter} mm" if diameter is not None else "-", font_box_title, font_box_value)
    draw_metric_box(draw, 260, 280, 230, 90, "Density", f"{density} g/cm³" if density is not None else "-", font_box_title, font_box_value)
    draw_metric_box(draw, 510, 280, 180, 90, "Nozzle", f"{nozzle}°C" if nozzle is not None else "-", font_box_title, font_box_value)
    draw_metric_box(draw, 710, 280, 210, 90, "Bed", f"{bed}°C" if bed is not None else "-", font_box_title, font_box_value)

    # Spool tracker
    section_y = 402
    draw.rounded_rectangle((40, section_y, 920, 610), radius=18, outline=180, width=2, fill=252)
    draw.text((58, section_y + 18), "SPOOL TRACKER", font=font_section, fill=0)

    box_y = section_y + 64
    box_w = 194
    gap = 18

    net_text = f"{net_weight} g" if net_weight is not None else "-"
    spool_text = f"{spool_weight} g" if spool_weight is not None else "-"
    remaining_text = f"{weight_left} g" if weight_left is not None else "-"
    if percent_left is not None:
        remaining_text = f"{remaining_text} ({percent_left:.0f}%)"
    length_text = f"{length_left} m" if length_left is not None else "-"

    draw_metric_box(draw, 58, box_y, box_w, 104, "Net Filament", net_text, font_box_title, font_box_value)
    draw_metric_box(draw, 58 + (box_w + gap), box_y, box_w, 104, "Spool Weight", spool_text, font_box_title, font_box_value)
    draw_metric_box(draw, 58 + 2 * (box_w + gap), box_y, box_w, 104, "Remaining", remaining_text, font_box_title, font_box_value)
    draw_metric_box(draw, 58 + 3 * (box_w + gap), box_y, box_w, 104, "Length Left", length_text, font_box_title, font_box_value)

    # Progress bar
    bar_x1, bar_y1, bar_x2, bar_y2 = 58, 568, 884, 590
    draw.rounded_rectangle((bar_x1, bar_y1, bar_x2, bar_y2), radius=10, fill=225)
    if percent_left is not None:
        fill_w = int((bar_x2 - bar_x1) * max(0, min(percent_left, 100)) / 100)
        draw.rounded_rectangle((bar_x1, bar_y1, bar_x1 + fill_w, bar_y2), radius=10, fill=0)

    # Footer
    now_text = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    draw.text((40, 632), f"Updated: {now_text}", font=font_footer, fill=0)
    draw.text((520, 632), f"ID: {instance_id}", font=font_footer, fill=0)

    img = img.point(lambda x: 0 if x < 170 else 255, "1").convert("L")
    img.save(outfile, "JPEG", quality=95)
    print(f"Saved {outfile}")


def upload_image():
    if not OUTPUT_IMAGE.exists():
        raise RuntimeError(f"Image not found: {OUTPUT_IMAGE}")

    upload_url = f"http://{OPENEPAPER_HOST}/imgupload"
    refresh_url = f"http://{OPENEPAPER_HOST}/tag_cmd"

    with open(OUTPUT_IMAGE, "rb") as f:
        files = {
            "file": (OUTPUT_IMAGE.name, f, "image/jpeg")
        }
        data = {
            "mac": TAG_MAC,
            "dither": "1",
            "contentmode": "24",
            "ttl": "0"
        }
        r = requests.post(upload_url, files=files, data=data, timeout=30)

    print("Upload status:", r.status_code)
    print("Upload response:", repr(r.text))
    r.raise_for_status()

    r2 = requests.post(
        refresh_url,
        data={"mac": TAG_MAC, "cmd": "refresh"},
        timeout=10
    )
    print("Refresh status:", r2.status_code)
    print("Refresh response:", repr(r2.text))
    r2.raise_for_status()


def read_tag_and_fetch_filament():
    r = readers()
    if not r:
        raise RuntimeError("No readers found")

    conn = r[0].createConnection()

    try:
        conn.connect()
    except NoCardException:
        print("No tag present")
        return None

    uid = transmit_ok(conn, GET_UID, "GET UID")
    print("UID:", hex_compact(uid))

    raw = read_multiple_blocks(conn, 0, 96)
    payload = find_openprinttag_payload(bytes(raw))
    objects = decode_cbor_sequence(payload)
    tag_fields = extract_openprinttag_fields(objects)

    instance_id = tag_fields.get("instance_id")
    print("Tag instance_id:", instance_id)

    filament = find_filament_by_instance_id(instance_id)
    return filament


def main():
    filament = read_tag_and_fetch_filament()
    if filament is None:
        return

    render_filament_image(filament, OUTPUT_IMAGE)
    upload_image()


if __name__ == "__main__":
    main()
