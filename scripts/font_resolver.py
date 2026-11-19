from pathlib import Path
import os


def cyrillic_font(*, bold: bool = False) -> Path:
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = (
        windows / ("arialbd.ttf" if bold else "arial.ttf"),
        Path("/System/Library/Fonts/Supplemental") / ("Arial Bold.ttf" if bold else "Arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu") / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
    )
    return next((path for path in candidates if path.is_file()), Path("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"))
