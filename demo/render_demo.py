"""Render the sanitized, evidence-based demo replay as an animated GIF."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
WIDTH, HEIGHT = 1280, 720
BACKGROUND = "#f5f3ed"
INK = "#17202d"
MUTED = "#687180"
CARD = "#111827"
ACCENT = "#aebcf7"
GREEN = "#91ddba"
RED = "#ff9d91"


def load_font(size: int, *, mono: bool = False, cjk: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if cjk:
        names = [
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/msyhbd.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
            Path("/usr/share/fonts/truetype/arphic/uming.ttc"),
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/System/Library/Fonts/STHeiti Light.ttc"),
        ]
    else:
        names = (
            [
                Path("C:/Windows/Fonts/consola.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
                Path("/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf"),
                Path("/System/Library/Fonts/Menlo.ttc"),
            ]
            if mono
            else [
                Path("C:/Windows/Fonts/segoeui.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
                Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            ]
        )
    for path in names:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    if cjk:
        raise RuntimeError("A CJK font is required for the Chinese replay (for example, Microsoft YaHei or Noto Sans CJK).")
    return ImageFont.load_default()


def wrap_pixels(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    rows: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > width:
            split_at = current.rfind(" ")
            if split_at > 0:
                rows.append(current[:split_at].rstrip())
                current = current[split_at + 1 :] + char
            else:
                rows.append(current.rstrip())
                current = char.lstrip()
        else:
            current = candidate
    if current:
        rows.append(current)
    return rows or [""]


def render_frame(frame: dict, footer: str, index: int, total: int, *, cjk: bool = False) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    sans = load_font(25, cjk=cjk)
    small = load_font(18, cjk=cjk)
    heading = load_font(43, cjk=cjk)
    mono = load_font(22, mono=True)
    mono_small = load_font(19, mono=True)
    terminal_font = load_font(18, cjk=True) if cjk else mono_small
    body_font = load_font(22, cjk=True) if cjk else mono

    # Header and progress
    draw.rounded_rectangle((76, 42, 118, 84), radius=11, fill="#273b91")
    draw.text((87, 45), "S", font=load_font(28), fill="#ffffff")
    draw.text((134, 51), "SPOT / ADVISOR", font=load_font(20), fill=INK)
    draw.text((1008, 56), "2.2 演示回放" if cjk else "2.2 DEMO REPLAY", font=small, fill=MUTED)
    draw.rounded_rectangle((76, 107, 1204, 113), radius=3, fill="#dedbd3")
    draw.rounded_rectangle((76, 107, 76 + int(1128 * index / total), 113), radius=3, fill="#596fe0")

    # Scene heading
    draw.text((78, 142), frame["label"], font=load_font(17, cjk=cjk), fill="#596fe0")
    draw.text((76, 172), frame["title"], font=heading, fill=INK)
    draw.text((78, 229), frame["subtitle"], font=sans, fill=MUTED)

    # Terminal card
    draw.rounded_rectangle((76, 286, 1204, 621), radius=18, fill=CARD)
    draw.rounded_rectangle((76, 286, 1204, 336), radius=18, fill="#1c2534")
    draw.rectangle((76, 318, 1204, 336), fill="#1c2534")
    for x, color in ((103, "#ff8176"), (123, "#f4c56a"), (143, "#76c99a")):
        draw.ellipse((x, 304, x + 10, 314), fill=color)
    draw.text((174, 298), frame["terminal"], font=terminal_font, fill="#d9dfeb")

    y = 355
    for tag, content in frame["lines"]:
        tag_color = RED if tag in {"FAIL", "失败", "-"} else GREEN if tag in {"PASS", "通过", "+", "完成"} else ACCENT
        draw.text((105, y), tag, font=body_font, fill=tag_color)
        body_x = 235
        lines = wrap_pixels(draw, content, body_font, 900)
        for offset, line in enumerate(lines):
            draw.text((body_x, y + offset * 29), line, font=body_font, fill="#eff2f7")
        y += max(39, 29 * len(lines) + 12)

    draw.text((78, 661), footer, font=small, fill=MUTED)
    draw.text((1114, 661), f"{index:02d} / {total:02d}", font=mono_small, fill=MUTED)
    return image


def main() -> None:
    for locale, source_name, target_name, cjk in (
        ("English", "replay.json", "spot-advisor-demo.gif", False),
        ("Chinese", "replay.zh-CN.json", "spot-advisor-demo.zh-CN.gif", True),
    ):
        data = json.loads((ROOT / source_name).read_text(encoding="utf-8"))
        frames = [
            render_frame(frame, data["footer"], index, len(data["frames"]), cjk=cjk)
            for index, frame in enumerate(data["frames"], start=1)
        ]
        palette_frames = [frame.convert("P", palette=Image.Palette.ADAPTIVE, colors=160) for frame in frames]
        target = ROOT / target_name
        palette_frames[0].save(
            target,
            save_all=True,
            append_images=palette_frames[1:],
            duration=[2100, 2400, 3000, 2300, 2400],
            loop=0,
            optimize=True,
            disposal=2,
        )
        print(f"Rendered {locale}: {len(frames)} frames, {target.name} ({target.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
