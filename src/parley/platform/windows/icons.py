# icons.py (Windows)
# Tray state icons drawn from the theme palette on demand — no static
# assets, so the tray always matches the app's design language. States:
# idle (quiet navy dot), recording (green 'listening' dot, the same signal
# as the overlay's status dot), processing (amber dot).

from PIL import Image, ImageDraw

from ... import theme


# Draw at 4× and downscale for clean anti-aliased edges at tray size.
def _dot_icon(fill: str, ring: str, size: int = 32, scale: int = 4) -> Image.Image:
    s = size * scale
    img = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = s // 8
    # Soft dark ring keeps the dot legible on both light and dark taskbars.
    d.ellipse((pad, pad, s - pad, s - pad), fill=ring)
    inner = pad + s // 10
    d.ellipse((inner, inner, s - inner, s - inner), fill=fill)
    return img.resize((size, size), Image.LANCZOS)


def get_tray_icons() -> dict:
    pal = theme.palette()
    return {
        "idle": _dot_icon(pal['fg_faint'], pal['bg_raised']),
        "recording": _dot_icon(pal['ok'], pal['bg_raised']),
        "processing": _dot_icon(pal['warn'], pal['bg_raised']),
    }
