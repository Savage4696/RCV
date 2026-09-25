"""Render synthetic sample photos for every bundled scenario (requires Pillow).

Each photo is drawn from the scenario's own observations, so the pictures show what the
recorded observations describe: carton counts, labels, units, colours, components and damage.
They are clearly marked as synthetic samples. Output: scenarios/photos/<scenario>/<photo_id>.png
"""

import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent / "scenarios"
W, H = 960, 640
COLORS = {
    "blue": (37, 99, 235),
    "red": (220, 38, 38),
    "green": (22, 163, 74),
    "teal": (13, 148, 136),
    "white": (240, 240, 235),
}
CARTON = (196, 154, 108)
CARTON_DARK = (150, 112, 74)


def font(size: int) -> ImageFont.ImageFont:
    for name in ("DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def cites(obj, pid) -> bool:
    return isinstance(obj, dict) and pid in (obj.get("photo_ids") or [])


def background(rng: random.Random) -> Image.Image:
    img = Image.new("RGB", (W, H), (120, 122, 126))
    d = ImageDraw.Draw(img)
    for y in range(H):
        shade = 150 - int(60 * y / H)
        d.line([(0, y), (W, y)], fill=(shade, shade + 2, shade + 6))
    for _ in range(900):
        x, y = rng.randrange(W), rng.randrange(H)
        g = rng.randrange(90, 170)
        d.point((x, y), fill=(g, g, g))
    d.rectangle([0, H - 120, W, H], fill=(96, 98, 102))
    return img


def draw_carton(d, box, damage, rng, idx):
    x0, y0, x1, y1 = box
    d.rectangle(box, fill=CARTON, outline=CARTON_DARK, width=3)
    mid = (x0 + x1) // 2
    d.rectangle([mid - 14, y0, mid + 14, y1], fill=(214, 184, 140))
    d.text((x0 + 10, y1 - 30), f"CARTON {idx + 1}", fill=(90, 60, 30), font=font(18))
    for dm in damage:
        kind = dm["type"]
        if kind == "crush":
            s = (x1 - x0) // 3
            d.polygon(
                [(x1 + 2, y0 - 2), (x1 - s, y0 - 2), (x1 - s // 2, y0 + s // 2), (x1 + 2, y0 + s)],
                fill=(120, 124, 128),
            )
            d.polygon(
                [
                    (x1 - s, y0),
                    (x1 - s // 2, y0 + s // 2),
                    (x1, y0 + s),
                    (x1 - s // 3, y0 + s // 2),
                ],
                fill=(140, 100, 62),
            )
            d.line(
                [(x1 - s, y0), (x1 - s // 2, y0 + s // 2), (x1, y0 + s)], fill=(60, 40, 20), width=6
            )
            d.line([(x1 - s, y0 + 30), (x1 - s // 3, y0 + s // 2 + 10)], fill=(90, 60, 30), width=4)
        elif kind == "water":
            for _ in range(5):
                cx, cy = rng.randint(x0 + 20, x1 - 20), rng.randint(y0 + 60, y1 - 10)
                r = rng.randint(22, 48)
                d.ellipse([cx - r, cy - r // 2, cx + r, cy + r // 2], fill=(128, 94, 62))
            d.line([(x0 + 5, y1 - 50), (x1 - 5, y1 - 58)], fill=(100, 70, 45), width=4)
        elif kind == "tear":
            pts, x, y = [], x0 + 30, y0 + 40
            while x < x1 - 30:
                pts.append((x, y + rng.randint(-12, 12)))
                x += 18
            d.line(pts, fill=(40, 25, 10), width=7)
            d.line([(p[0], p[1] + 6) for p in pts], fill=(235, 225, 205), width=3)
        elif kind == "puncture":
            cx, cy = (x0 + x1) // 2 + 40, (y0 + y1) // 2
            d.ellipse([cx - 16, cy - 12, cx + 16, cy + 12], fill=(25, 20, 15))
        else:
            d.line([(x0 + 20, y0 + 20), (x1 - 20, y1 - 40)], fill=(70, 50, 30), width=4)


def closeup_scene(img, obs, pid, rng):
    d = ImageDraw.Draw(img)
    dmg = [x for x in obs.get("damage", []) if cites(x, pid) and x["target"] != "product"]
    draw_carton(d, (120, 70, W - 120, H - 60), dmg, rng, 0)


def cartons_scene(img, obs, pid, rng):
    d = ImageDraw.Draw(img)
    n = obs["cartons"]["count"]["count"] or 2
    n = max(1, min(n, 6))
    dmg = [x for x in obs.get("damage", []) if cites(x, pid) and x["target"] != "product"]
    cols = min(n, 3)
    rows = (n + cols - 1) // cols
    bw, bh = min(380, (W - 120) // cols - 30), min(320, 460 // rows - 10)
    for i in range(n):
        r, c = divmod(i, cols)
        x0 = 60 + c * (bw + 30)
        y0 = H - 110 - (rows - r) * (bh + 10)
        mine = [x for j, x in enumerate(dmg) if j % n == i] if n > 1 else dmg
        draw_carton(d, (x0, y0, x0 + bw, y0 + bh), mine, rng, i)
        if obs["cartons"].get("sealed"):
            d.line([(x0, y0 + 6), (x0 + bw, y0 + 6)], fill=(170, 150, 110), width=8)


def label_scene(img, obs, pid, sku_line, catalog_item):
    d = ImageDraw.Draw(img)
    d.rectangle([90, 60, W - 90, H - 140], fill=CARTON, outline=CARTON_DARK, width=4)
    d.rectangle([180, 110, W - 180, H - 190], fill=(250, 250, 248), outline=(40, 40, 40), width=3)
    y = 130
    ids = [i for i in obs.get("identifiers", []) if cites(i, pid)]
    names = [i["value"] for i in ids if i["kind"] == "product_name"]
    skus = [i["value"] for i in ids if i["kind"] == "sku"]
    codes = [i["value"] for i in ids if i["kind"] in ("barcode", "asin")]
    if names or (catalog_item and not skus):
        d.text(
            (210, y), (names or [catalog_item["name"]])[0][:44], fill=(20, 20, 20), font=font(20)
        )
        y += 44
    for s in skus:
        d.text((210, y), f"SKU: {s}", fill=(0, 0, 0), font=font(34))
        y += 52
    c = obs["cartons"]
    if cites({"photo_ids": c.get("units_per_carton_label_photo_ids")}, pid):
        d.text((210, y), f"QTY: {c['units_per_carton_label']} UNITS", fill=(0, 0, 0), font=font(30))
        y += 48
    v = obs.get("variant", {})
    if cites(v, pid) and v.get("attributes", {}).get("color"):
        color = v["attributes"]["color"]
        d.rectangle([210, y, 250, y + 30], fill=COLORS.get(color, (128, 128, 128)))
        d.text((262, y), f"COLOUR: {color.upper()}", fill=(0, 0, 0), font=font(26))
        y += 44
    for code in codes[:1]:
        x = 210
        rng = random.Random(code)
        while x < W - 220:
            w = rng.choice([2, 3, 5])
            d.rectangle([x, y + 6, x + w, y + 70], fill=(0, 0, 0))
            x += w + rng.choice([2, 3, 4])
        d.text((210, y + 76), code, fill=(0, 0, 0), font=font(22))


def units_scene(img, obs, pid, catalog_item, rng):
    d = ImageDraw.Draw(img)
    n = obs["units"]["count"] if cites(obs["units"], pid) else None
    n = n or 12
    color_name = (obs.get("variant", {}).get("attributes") or {}).get("color", "blue")
    color = COLORS.get(color_name, (120, 120, 120))
    comps = {c["name"]: c for c in obs.get("components", []) if cites(c, pid)}
    lamp = catalog_item and "lamp base" in catalog_item.get("components", [])
    damaged = [x for x in obs.get("damage", []) if cites(x, pid)]
    other = [x for x in obs.get("other_issues", []) if cites(x, pid)]
    if lamp:
        for i in range(min(n, 4)):
            x0 = 40 + i * 225
            d.rectangle(
                [x0, 150, x0 + 205, 470], fill=(236, 230, 220), outline=(120, 110, 100), width=3
            )
            d.text((x0 + 10, 160), f"UNIT {i + 1}", fill=(60, 60, 60), font=font(18))
            parts = [
                ("lamp base", 400, 60),
                ("lamp arm", 300, 22),
                ("shade", 215, 70),
                ("power adapter", 420, 30),
            ]
            for name, cy, w in parts:
                missing = name in comps and comps[name]["present"] is False and i == 2
                if missing:
                    d.rectangle(
                        [x0 + 102 - w // 2, cy - 22, x0 + 102 + w // 2, cy + 22],
                        outline=(160, 160, 160),
                        width=2,
                    )
                    d.text((x0 + 60, cy - 10), "EMPTY", fill=(180, 40, 40), font=font(16))
                else:
                    d.rectangle(
                        [x0 + 102 - w // 2, cy - 20, x0 + 102 + w // 2, cy + 20],
                        fill=COLORS["white"],
                        outline=(90, 90, 90),
                        width=2,
                    )
        return
    cols = min(12, n)
    bw = (W - 80) // cols
    for i in range(n):
        r, c = divmod(i, cols)
        x0 = 40 + c * bw + 6
        y0 = 120 + r * 150
        d.rounded_rectangle(
            [x0, y0 + 26, x0 + bw - 14, y0 + 136],
            radius=12,
            fill=color,
            outline=(30, 30, 30),
            width=2,
        )
        if "lid" not in comps or comps["lid"]["present"] is not False:
            d.rectangle([x0 + 8, y0 + 6, x0 + bw - 22, y0 + 28], fill=(20, 20, 20))
        if damaged and i in (1, 5, 9):
            for dm in damaged:
                if dm["type"] == "tear":
                    d.line(
                        [(x0 + 4, y0 + 60), (x0 + bw // 2, y0 + 90), (x0 + bw - 18, y0 + 70)],
                        fill=(250, 250, 250),
                        width=4,
                    )
                elif dm["type"] == "puncture" and i == 1:
                    d.ellipse([x0 + 16, y0 + 70, x0 + 36, y0 + 92], fill=(10, 10, 10))
        if other and i in (3, 7):
            d.line([(x0 + 6, y0 + 50), (x0 + bw - 20, y0 + 120)], fill=(210, 210, 210), width=3)
            d.line([(x0 + 10, y0 + 70), (x0 + bw - 24, y0 + 110)], fill=(210, 210, 210), width=2)


def render(scenario: dict, catalog: list[dict], name: str, pid: str) -> Image.Image:
    obs = scenario["observations"]
    rng = random.Random(f"{name}:{pid}")
    sku = scenario["purchase_order"]["lines"][0]["sku"]
    item = next((c for c in catalog if c["sku"] == sku), None)
    review = next((p for p in obs.get("photos", []) if p["photo_id"] == pid), {})
    img = background(rng)
    shows = " ".join(review.get("shows", [])).lower()
    if "label" in shows or "print" in shows:
        label_scene(img, obs, pid, sku, item)
    elif cites(obs["units"], pid) or "unit" in shows or "open" in shows:
        units_scene(img, obs, pid, item, rng)
    elif "close-up" in shows and not cites(obs["cartons"]["count"], pid):
        closeup_scene(img, obs, pid, rng)
    else:
        cartons_scene(img, obs, pid, rng)
    quality = " ".join(review.get("quality_issues", [])).lower()
    if not review.get("usable", True) or "blur" in quality:
        img = img.filter(ImageFilter.GaussianBlur(9))
    if "dark" in quality or "lighting" in quality:
        img = Image.eval(img, lambda v: v // 3)
    if "far" in quality:
        small = img.resize((W // 4, H // 4))
        img = background(rng)
        img.paste(small, (W // 2 - W // 8, H // 2 - H // 8))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 34], fill=(17, 24, 39))
    d.text((12, 7), f"SYNTHETIC SAMPLE  {name}  {pid}", fill=(255, 255, 255), font=font(18))
    return img


def main() -> int:
    catalog = json.loads((ROOT / "catalog.json").read_text())
    for path in sorted(ROOT.glob("*.json")):
        if path.name == "catalog.json":
            continue
        scenario = json.loads(path.read_text())
        out = ROOT / "photos" / path.stem
        out.mkdir(parents=True, exist_ok=True)
        for pid in scenario["photos"]:
            render(scenario, catalog, path.stem, pid).save(out / f"{pid}.png", optimize=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
