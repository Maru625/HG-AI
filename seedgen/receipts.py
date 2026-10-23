"""시연용 가상 영수증 이미지를 그린다.

- 상호는 모두 가상이고, 이미지마다 'DEMO · 시연용 가상 영수증' 표시를 넣는다.
- 실제 사업자 정보는 쓰지 않는다(사업자번호는 000-00-00000).
- blur 값이 있으면 흐리게 만들어 판독이 애매하게 한다(설계서 8.3).
"""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\malgun.ttf",
    r"C:\Windows\Fonts\malgunbd.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
]


def find_font(explicit: str | None = None) -> str:
    for p in ([explicit] if explicit else []) + FONT_CANDIDATES:
        if p and os.path.exists(p):
            return p
    raise SystemExit("한글 글꼴을 찾지 못했습니다. --font 로 .ttf/.ttc 경로를 지정하세요. (예: C:\\Windows\\Fonts\\malgun.ttf)")


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    index = 1 if path.endswith(".ttc") and "NotoSansCJK" in path else 0  # Noto CJK .ttc의 1번이 KR
    try:
        return ImageFont.truetype(path, size, index=index)
    except OSError:
        return ImageFont.truetype(path, size)


def draw_receipt(r: dict, out: Path, font_path: str) -> None:
    W, H = 440, 660
    img = Image.new("RGB", (W, H), (252, 252, 248))
    d = ImageDraw.Draw(img)
    f_title, f_body, f_small, f_total = (_font(font_path, s) for s in (28, 19, 15, 26))
    y = 26
    d.text((W // 2, y), r["merchant"], font=f_title, fill=(20, 20, 20), anchor="ma")
    y += 46
    for line in ["사업자번호 000-00-00000 (가상)", "주소: 시연용 가상 주소", "TEL 000-0000-0000"]:
        d.text((W // 2, y), line, font=f_small, fill=(70, 70, 70), anchor="ma")
        y += 22
    y += 8
    d.line((24, y, W - 24, y), fill=(120, 120, 120), width=1)
    y += 16
    d.text((28, y), f"일시  {r['date']} {r.get('time', '')}", font=f_body, fill=(20, 20, 20))
    y += 32
    d.text((28, y), f"구분  {r['category']}", font=f_body, fill=(20, 20, 20))
    y += 40
    for line in r.get("lines", []):
        d.text((28, y), line, font=f_body, fill=(20, 20, 20))
        y += 30
    if r.get("nights"):
        d.text((28, y), f"숙박 {r['nights']}박", font=f_body, fill=(20, 20, 20))
        y += 30
    y += 12
    d.line((24, y, W - 24, y), fill=(120, 120, 120), width=1)
    y += 18
    d.text((28, y), "합계", font=f_total, fill=(10, 10, 10))
    d.text((W - 28, y), f"{r['amount']:,}원", font=f_total, fill=(10, 10, 10), anchor="ra")
    y += 52
    d.text((28, y), "카드 ****-****-****-0000 (DEMO)", font=f_small, fill=(70, 70, 70))
    y += 22
    d.text((28, y), "승인번호 00000000", font=f_small, fill=(70, 70, 70))

    # 워터마크
    mark = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    md = ImageDraw.Draw(mark)
    md.text((W // 2, H // 2), "DEMO · 시연용 가상 영수증", font=_font(font_path, 30), fill=(200, 40, 40, 70), anchor="mm")
    mark = mark.rotate(28, resample=Image.BICUBIC)
    img = Image.alpha_composite(img.convert("RGBA"), mark).convert("RGB")
    d = ImageDraw.Draw(img)
    d.text((W // 2, H - 28), "DEMO · 시연용 가상 영수증", font=f_small, fill=(180, 40, 40), anchor="ma")

    if r.get("blur"):
        img = img.filter(ImageFilter.GaussianBlur(radius=float(r["blur"])))
    img.save(out, format="PNG", optimize=False)


def draw_all(receipts: list[dict], out_dir: Path, font: str | None = None) -> None:
    font_path = find_font(font)
    for r in receipts:
        draw_receipt(r, out_dir / r["file"], font_path)
