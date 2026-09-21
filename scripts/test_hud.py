"""P7 HUD test: compose + live status panel + REAL agent turn through the UI path."""
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sky_hud import SkyHud  # noqa: E402
from sky import load_cfg  # noqa: E402
from core import memory  # noqa: E402
from textual.widgets import Input  # noqa: E402


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="sky_hud_t_"))
    cfg = load_cfg()
    cfg["speak_replies"] = False
    mem = memory.Memory(tmp / "memory.db")
    app = SkyHud(cfg, mem)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.pause(2.2)  # let status interval fire once
        print("status:", app.last_status.replace("\n", " | "))
        assert "brain" in app.last_status and "cpu" in app.last_status.lower()

        inp = app.query_one("#cmd", Input)
        inp.focus()
        for ch in "Reply with exactly: HUD LINK OK":
            await pilot.press(ch)
        await pilot.press("enter")
        for _ in range(150):  # up to ~75s for the LLM
            await asyncio.sleep(0.5)
            if app.replies:
                break
        print("reply:", app.replies[-1] if app.replies else "NONE")
    mem.close()
    shutil.rmtree(tmp, ignore_errors=True)
    ok = bool(app.replies) and "HUD LINK OK" in app.replies[-1]
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


asyncio.run(main())
