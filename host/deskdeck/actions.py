"""Actions that run on the Mac: track control via media-control, volume via osascript,
shortcuts via open -a, open, osascript and shortcuts run."""
import asyncio
import logging

from .config import ButtonSpec

log = logging.getLogger(__name__)

VOLUME_STEP = 6  # on the 0-100 scale; ~16 steps
MEDIA_COMMANDS = {"prev": "previous-track", "playpause": "toggle-play-pause", "next": "next-track"}


async def run(*cmd: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode:
        log.warning("%s → exit %s: %s", " ".join(cmd), proc.returncode, err.decode(errors="replace").strip())


async def media(command: str) -> None:
    await run("media-control", command)


async def volume(delta: int) -> None:
    op = f"+ {delta}" if delta >= 0 else f"- {-delta}"
    await run("osascript", "-e", f"set volume output volume ((output volume of (get volume settings)) {op})")


async def toggle_mute() -> None:
    await run("osascript", "-e", "set volume output muted (not (output muted of (get volume settings)))")


async def perform(spec: ButtonSpec) -> None:
    match spec.action, spec.arg:
        case "media", ("prev" | "playpause" | "next") as kind:
            await media(MEDIA_COMMANDS[kind])
        case "media", "vol_up":
            await volume(VOLUME_STEP)
        case "media", "vol_down":
            await volume(-VOLUME_STEP)
        case "media", "mute":
            await toggle_mute()
        case "app", name:
            await run("open", "-a", name)
        case "open", target:
            await run("open", target)
        case "osascript", script:
            await run("osascript", "-e", script)
        case "shortcut", name:
            await run("shortcuts", "run", name)
