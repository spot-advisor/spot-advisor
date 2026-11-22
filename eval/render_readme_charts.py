"""Render both READMEs' token charts from the committed formal collection.

Requires Pillow, jsonschema and a font with Simplified Chinese glyphs. For example, on Windows:
python eval/render_readme_charts.py --font C:/Windows/Fonts/msyh.ttc
"""

import argparse
from math import fsum
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from report import estimate, load_pairs, read_json, requests


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "eval/results/2026-11-20/formal-collection"
ASSETS = ROOT / "docs/assets"
INK = "#103438"
MUTED = "#52696a"
TEAL = "#087970"
AMBER = "#e9a42f"
COPY = {
    "en": (
        "Model cost and token usage",
        "Subtotals from recorded receipts. Each panel has its own linear scale.",
        ("Estimated API-equivalent cost", "Frontier tokens (Astra only)", "Total tokens (Astra + Luna)"),
        ("Astra alone", "Luna Max + Spot Advisor"),
        ("USD", "Millions of tokens", "Millions of tokens"),
    ),
    "zh-CN": (
        "模型成本与 Token 用量",
        "只统计已记录的请求用量。各图使用各自的线性刻度。",
        ("按 API 费率估算的成本", "前沿模型用量（仅 Astra）", "总用量（Astra + Luna）"),
        ("Astra 独立执行", "Luna Max + Spot Advisor"),
        ("美元", "百万 Token", "百万 Token"),
    ),
}


def render(language, font_path, observations):
    heading, subtitle, titles, labels, units = COPY[language]
    image = Image.new("RGB", (1100, 808), "#fff4df")
    draw = ImageDraw.Draw(image)
    heading_font = ImageFont.truetype(font_path, 40)
    panel_font = ImageFont.truetype(font_path, 30)
    label_font = ImageFont.truetype(font_path, 25)
    tick_font = ImageFont.truetype(font_path, 21)
    draw.text((40, 34), heading, font=heading_font, fill=INK)
    draw.text((42, 92), subtitle, font=label_font, fill=MUTED)

    for index, (title, unit, step) in enumerate(zip(titles, units, (30, 4, 20))):
        top = 144 + index * 218
        left, right = 372, 916
        bottom = top + 156
        draw.rounded_rectangle((26, top, 1074, top + 204), radius=22, fill="#fffdfa")
        draw.text((48, top + 18), title, font=panel_font, fill=INK)
        draw.text((1048, top + 26), unit, font=tick_font, fill=MUTED, anchor="rt")

        for tick in range(4):
            x = left + (right - left) * tick / 3
            draw.line((x, top + 62, x, bottom), fill="#dce6e0", width=2)
            draw.text((x, bottom + 10), str(tick * step),
                      font=tick_font, fill=MUTED, anchor="mt")

        for row, (label, values, color) in enumerate(zip(labels, observations, (TEAL, AMBER))):
            y = top + 81 + row * 47
            value = values[index]
            end = left + (right - left) * value / (3 * step)
            draw.text((48, y), label, font=label_font, fill=INK, anchor="lm")
            draw.rectangle((left, y - 11, end, y + 11), fill=color)
            if index == 0:
                value_label = f"${value:.2f}"
            else:
                value_label = f"{value:.4f}M" if value < 0.1 else f"{value:.2f}M"
            draw.text((1048, y), value_label, font=label_font, fill=INK, anchor="rm")

    image.save(ASSETS / f"token-comparison.{language}.png", optimize=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", required=True, help="Path to a Simplified Chinese font")
    args = parser.parse_args()
    metrics = read_json(SNAPSHOT / "metrics.json")
    receipts = metrics["deployment"]["received"]
    _, rows, _ = load_pairs(SNAPSHOT)
    observations = []
    for arm in ("astra_only", "spot_advisor"):
        prices = [
            estimate(request)
            for row in rows
            if row["arm"] == arm and row["suite"] == "swe-bench-pro-v2"
            for request in requests(row)
        ]
        subtotal = fsum(price for price in prices if price is not None)
        frontier = receipts[arm]["astra_tokens"] / 1_000_000
        total = (receipts[arm]["astra_tokens"] + receipts[arm]["luna_tokens"]) / 1_000_000
        observations.append((subtotal, frontier, total))
    ASSETS.mkdir(parents=True, exist_ok=True)
    for language in COPY:
        render(language, args.font, observations)


if __name__ == "__main__":
    main()
