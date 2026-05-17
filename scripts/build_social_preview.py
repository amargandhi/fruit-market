#!/usr/bin/env python3
"""Render the GitHub repo social-preview banner from the brand icon.

GitHub's "Social preview" image is the 1280×640 picture that
appears in unfurled links on Slack, Twitter, Discord, etc. It's
uploaded via Settings → General → Social preview on the repo; the
file itself doesn't live in the repo, but we keep a reproducible
source here so any team member can regenerate it.

Inputs (defaults assume project root):
    docs/assets/fruit-market-icon.png   — 1024² brand master

Output:
    docs/branding/github-social-preview.png  — 1280×640 banner

Run from project root:
    python scripts/build_social_preview.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


# Match the kiosk's brand palette (fruit_market/ui/styles.css).
BG_COLOR = "#0f1614"          # --bg
ACCENT = "#7fc88a"            # --accent
TEXT = "#e8efe9"              # --text
MUTED = "#8a9b91"             # --muted

CANVAS_W, CANVAS_H = 1280, 640
LOGO_BOX = 480                # square logo on the left
PADDING = 80


def _load_font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    """Find a system font that exists on macOS + Linux CI."""

    # Apple SF Pro is the kiosk's primary; falls back are widely
    # available so the script runs in CI too.
    candidates = {
        "bold": [
            "/System/Library/Fonts/SFNS.ttf",
            "/Library/Fonts/SF-Pro-Display-Bold.otf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ],
        "regular": [
            "/System/Library/Fonts/SFNS.ttf",
            "/Library/Fonts/SF-Pro-Display-Regular.otf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ],
    }
    for path in candidates[weight]:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    # Last-resort built-in bitmap font — ugly but the script
    # still produces output.
    return ImageFont.load_default()


def build_preview(master_png: Path, output_png: Path) -> None:
    """Compose the 1280×640 banner."""

    logo = Image.open(master_png).convert("RGBA")
    logo = logo.resize((LOGO_BOX, LOGO_BOX), Image.LANCZOS)

    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), BG_COLOR)

    # Subtle accent corner glow so the banner doesn't read as a
    # solid black rectangle on dark Slack themes.
    glow = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    for radius, alpha in [(400, 18), (260, 28), (140, 40)]:
        glow_draw.ellipse(
            [
                (CANVAS_W - radius, CANVAS_H - radius),
                (CANVAS_W + radius, CANVAS_H + radius),
            ],
            fill=(127, 200, 138, alpha),
        )
    canvas.paste(glow, (0, 0), glow)

    # Logo on the left, vertically centred.
    canvas.paste(logo, (PADDING, (CANVAS_H - LOGO_BOX) // 2), logo)

    # Wordmark + tagline on the right.
    draw = ImageDraw.Draw(canvas)
    title_font = _load_font("bold", 108)
    tag_font = _load_font("regular", 36)
    sub_font = _load_font("regular", 28)

    text_x = PADDING + LOGO_BOX + 60
    title_y = 180
    draw.text((text_x, title_y), "Fruit Market", fill=TEXT, font=title_font)
    draw.text(
        (text_x, title_y + 140),
        "Physical AI agent",
        fill=ACCENT,
        font=tag_font,
    )
    draw.text(
        (text_x, title_y + 190),
        "Vision · Voice · Payments",
        fill=MUTED,
        font=sub_font,
    )

    output_png.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_png, "PNG", optimize=True)
    print(f"wrote {output_png} ({canvas.size[0]}x{canvas.size[1]})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--master",
        type=Path,
        default=Path("docs/assets/fruit-market-icon.png"),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("docs/branding/github-social-preview.png"),
    )
    args = parser.parse_args(argv)

    if not args.master.exists():
        print(f"missing master image: {args.master}", file=sys.stderr)
        return 2
    build_preview(args.master, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
