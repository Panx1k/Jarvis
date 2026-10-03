"""Сборка Jarvis.exe — маленького запускающего файла (без консоли) из launcher/JarvisLauncher.cs.

Используется компилятор C#, который уже есть в Windows (.NET Framework 4), ничего ставить не нужно:
    .venv\\Scripts\\python scripts\\build_exe.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "launcher" / "JarvisLauncher.cs"
ICON = ROOT / "assets" / "jarvis.ico"
TARGET = ROOT / "Jarvis.exe"


def make_icon(path: Path) -> None:
    """Иконка — светящееся ядро JARVIS, как в трее."""
    from PIL import Image, ImageDraw

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    center, radius = size / 2, size / 2 - 8
    stops = [(0.0, (255, 255, 255)), (0.35, (0, 216, 255)), (1.0, (0, 40, 70))]
    for i in range(int(radius), 0, -1):
        t = i / radius
        for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
            if p0 <= t <= p1:
                k = (t - p0) / (p1 - p0)
                color = tuple(int(a + (b - a) * k) for a, b in zip(c0, c1))
                break
        draw.ellipse((center - i, center - i, center + i, center + i), fill=color + (255,))
    draw.ellipse((center - radius, center - radius, center + radius, center + radius), outline=(0, 216, 255, 255),
                 width=6)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def find_csc() -> Path:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    for framework in ("Framework64", "Framework"):
        for csc in sorted((windir / "Microsoft.NET" / framework).glob("v4*/csc.exe"), reverse=True):
            return csc
    raise SystemExit("Не найден компилятор C# (.NET Framework 4). Он есть в любой Windows 10/11.")


def main() -> None:
    if not ICON.exists():
        make_icon(ICON)
    csc = find_csc()
    cmd = [str(csc), "/nologo", "/target:winexe", "/optimize+", "/codepage:65001", f"/out:{TARGET}",
           f"/win32icon:{ICON}", "/reference:System.Management.dll", "/reference:System.Windows.Forms.dll",
           str(SOURCE)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="cp866", errors="replace")
    if result.returncode != 0:
        print(result.stdout or result.stderr)
        sys.exit(1)
    print(f"Готово: {TARGET} ({TARGET.stat().st_size // 1024} КБ)")


if __name__ == "__main__":
    main()
