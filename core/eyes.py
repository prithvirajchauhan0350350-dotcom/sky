"""P4 eyes: screenshot the Windows desktop, then understand it.

Runs natively on Windows Python: PowerShell captures the screen and also
writes a downscaled copy (System.Drawing — there is no ffmpeg on Windows).
Understanding chain (first available wins):
1. vision model if config.vision_model is set (OmniRoute currently has no
   vision credentials — so usually skipped)
2. local OCR via rapidocr (the package was rapidocr-onnxruntime pre-3.13)
3. honest "cannot see" message — never faked
"""
import logging
import os
from pathlib import Path

log = logging.getLogger("sky.eyes")

_PS_SCRIPT = """
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
$out = Join-Path $env:TEMP 'sky_screen.png'
$bmp.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
# downscaled copy for OCR / vision (no ffmpeg on Windows)
$ratio = 1280.0 / [Math]::Max($b.Width, 1)
if ($ratio -lt 1) {
  $sw = [int]($b.Width * $ratio)
  $sh = [int]($b.Height * $ratio)
  $small = New-Object System.Drawing.Bitmap $sw, $sh
  $g2 = [System.Drawing.Graphics]::FromImage($small)
  $g2.DrawImage($bmp, 0, 0, $sw, $sh)
  $smallOut = Join-Path $env:TEMP 'sky_screen_small.png'
  $small.Save($smallOut, [System.Drawing.Imaging.ImageFormat]::Png)
  Write-Output $smallOut
}
Write-Output $out
"""


def capture() -> tuple:
    """Screenshot via PowerShell -> (full png, small png) in Windows temp."""
    import subprocess

    # pass the script as an argument, not stdin — stdin piping breaks under
    # WSL interop (argument form works everywhere)
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", _PS_SCRIPT],
                       capture_output=True, text=True, timeout=60)
    lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    if not lines or r.returncode != 0:
        raise RuntimeError(f"screenshot failed: {(r.stderr or r.stdout)[:200]}")
    full = Path(lines[-1])
    # the optional downscaled path is printed just before the full one
    small = Path(lines[-2]) if len(lines) >= 2 else full
    if not full.exists():
        raise RuntimeError(f"screenshot not found at {full}")
    if not small.exists():
        small = full
    return full, small


def _ocr(image: Path) -> str:
    try:
        try:  # new package name (Python >= 3.13)
            from rapidocr import RapidOCR
        except ImportError:  # legacy name
            from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
        result = engine(str(image))
        # rapidocr>=3 returns [image, [boxes, text], elapsed]; v1 returned (result, elapse)
        if isinstance(result, (list, tuple)) and result and isinstance(result[0], (list, tuple)) and result[0] and not isinstance(result[0][0], str):
            result = result[1]
        elif isinstance(result, (list, tuple)) and len(result) == 2 and result[0] is not None and isinstance(result[0][0], str):
            result = result[0]
        if not result:
            return ""
        items = result if isinstance(result, list) else list(result)
        return " | ".join(
            (item[1] if isinstance(item, (list, tuple)) else str(item))
            for item in items[:80])[:1800]
    except Exception as e:
        log.warning("OCR unavailable: %s", e)
        return ""


def understand(question: str, cfg: dict) -> str:
    """Full `sky see` turn. Returns an honest description of the screen."""
    full, small = capture()
    answer = [f"Screenshot captured: {full}"]

    vision_model = cfg.get("vision_model") or os.environ.get("SKY_VISION_MODEL", "")
    if vision_model:
        import base64
        from . import llm
        b64 = base64.b64encode(small.read_bytes()).decode()
        try:
            reply = llm.chat_raw(cfg, [
                {"role": "user", "content": [
                    {"type": "text", "text": question or "What is on this screen?"},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}}]}],
                model=vision_model, max_tokens=600)
            answer.append(reply.get("content") or "(vision model returned nothing)")
            return "\n".join(answer)
        except Exception as e:
            answer.append(f"(vision model {vision_model} failed: {str(e)[:120]})")

    text = _ocr(small)
    if text:
        answer.append(f"Text visible on screen (OCR): {text}")
    else:
        answer.append(
            "No vision model is configured and OCR found nothing — I cannot "
            "describe the screen, sir. Add a vision-capable model to the router "
            "and set config.json -> vision_model, and I will truly see.")
    return "\n".join(answer)
