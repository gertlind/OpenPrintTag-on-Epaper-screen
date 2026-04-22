from pathlib import Path
import requests

OPENEPAPER_HOST = "192.168.123.90"
TAG_MAC = "000001848805411C"

BASE_DIR = Path(__file__).resolve().parent
IMAGE_FILE = BASE_DIR / "current_tag.jpg"   # byt till .png om det är den du sparar

UPLOAD_URL = f"http://{OPENEPAPER_HOST}/imgupload"
REFRESH_URL = f"http://{OPENEPAPER_HOST}/tag_cmd"


def main():
    if not IMAGE_FILE.exists():
        print("Image not found:", IMAGE_FILE)
        return

    mime_type = "image/jpeg" if IMAGE_FILE.suffix.lower() == ".jpg" else "image/png"

    with open(IMAGE_FILE, "rb") as f:
        files = {
            "file": (IMAGE_FILE.name, f, mime_type)
        }
        data = {
            "mac": TAG_MAC,
            "dither": "1",
            "contentmode": "24",
            "ttl": "0"
        }

        r = requests.post(UPLOAD_URL, files=files, data=data, timeout=30)

    print("Upload status:", r.status_code)
    print("Upload response:", repr(r.text))

    # valfritt: be AP att markera taggen för refresh
    r2 = requests.post(
        REFRESH_URL,
        data={"mac": TAG_MAC, "cmd": "refresh"},
        timeout=10
    )

    print("Refresh status:", r2.status_code)
    print("Refresh response:", repr(r2.text))


if __name__ == "__main__":
    main()
