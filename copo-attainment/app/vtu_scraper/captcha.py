"""
Solves the CAPTCHA image on VTU's results portal login form via local OCR
(Tesseract) - no manual typing, no third-party captcha-solving API, no ML
model. Adapted from the pixel-filtering + pytesseract approach in
nithinhm/vtu-marks-scraper-analyzer's captcha_handler.py (GPL v2), whose
own testing found that VTU's captcha renders its digits/letters as a
narrow band of mid-gray anti-aliased pixels on a noisy background - simply
keeping only pixels in that gray range and dropping everything else onto a
white canvas turns out to be enough preprocessing for Tesseract to read it
reliably, without needing a trained model.

The one thing that had to change: the original hardcodes a Windows path
to a bundled Tesseract-OCR folder (`Tesseract-OCR\\tesseract.exe`), which
doesn't exist here. `find_tesseract()` below looks for a real Tesseract
install instead (Homebrew on Mac, apt on Linux, or just PATH), the same
"probe a few known locations, fall back to PATH" pattern used for
LibreOffice elsewhere in this app.
"""
import shutil
import sys
from io import BytesIO

from PIL import Image

# VTU's captcha renders its characters in this narrow mid-gray band (RGB
# grayscale pixels, so (i, i, i)) against a noisier background - keeping
# only pixels in this range and dropping the rest is the entire
# "preprocessing" step. Tuned empirically by the source repo's author
# against VTU's actual captcha images, not a guess.
_TEXT_PIXEL_RANGE = frozenset((i, i, i) for i in range(100, 160))

_KNOWN_TESSERACT_PATHS = [
    "/opt/homebrew/bin/tesseract",  # Homebrew on Apple Silicon Macs
    "/usr/local/bin/tesseract",  # Homebrew on Intel Macs
    "/usr/bin/tesseract",  # apt on Linux
]
if sys.platform == "win32":
    _KNOWN_TESSERACT_PATHS += [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]


def find_tesseract():
    """Best-effort discovery of a real Tesseract binary on this machine.
    Returns a path string, or None if nothing was found - callers should
    treat None as "tell the user to `brew install tesseract` (Mac) or
    `apt install tesseract-ocr` (Linux) and try again", not crash."""
    on_path = shutil.which("tesseract")
    if on_path:
        return on_path
    for candidate in _KNOWN_TESSERACT_PATHS:
        if shutil.which(candidate) or __import__("os").path.isfile(candidate):
            return candidate
    return None


class TesseractNotFoundError(RuntimeError):
    """Raised when no local Tesseract install could be located - the
    caller should surface this as a setup instruction, not a stack trace."""


class CaptchaSolver:
    """Turns one CAPTCHA image (PNG bytes, as screenshotted straight off
    the page element) into the guessed text. Stateless/reusable across
    calls - construct once per scrape run."""

    def __init__(self, tesseract_cmd=None):
        cmd = tesseract_cmd or find_tesseract()
        if not cmd:
            raise TesseractNotFoundError(
                "Tesseract OCR isn't installed (or wasn't found on PATH). "
                "On a Mac: `brew install tesseract`. On Linux: "
                "`sudo apt install tesseract-ocr`. Then try again."
            )
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = cmd
        self._pytesseract = pytesseract

    def solve(self, image_bytes):
        """image_bytes: raw PNG/JPEG bytes of the captcha image. Returns
        the OCR'd text, stripped of surrounding whitespace - callers
        should treat anything not exactly 6 characters as a bad read and
        retry against a freshly reloaded captcha, same as the source
        repo's own validation."""
        image = Image.open(BytesIO(image_bytes))
        width, height = image.size
        image = image.convert("RGB")

        filtered = Image.new("RGB", (width, height), "white")
        pixels = image.load()
        filtered_pixels = filtered.load()
        for x in range(width):
            for y in range(height):
                pixel = pixels[x, y]
                if pixel in _TEXT_PIXEL_RANGE:
                    filtered_pixels[x, y] = pixel

        return self._pytesseract.image_to_string(filtered).strip()
