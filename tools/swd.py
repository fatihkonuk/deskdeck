"""Reads/writes the running firmware's RAM through ST-Link/openocd (the target is not halted)."""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NM = pathlib.Path.home() / ".platformio/packages/toolchain-gccarmnoneeabi/bin/arm-none-eabi-nm"


def elf_path(env: str) -> pathlib.Path:
    return ROOT / f"firmware/.pio/build/{env}/firmware.elf"


def symbol(name: str, env: str = "harness") -> tuple[int, int]:
    """Address and size of a global in the ELF. env: PlatformIO environment (must match what is flashed)."""
    elf = elf_path(env)
    out = subprocess.run([NM, "-S", elf], capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[3] == name:
            return int(parts[0], 16), int(parts[1], 16)
    sys.exit(f"{name} not found in {elf} (is the right environment flashed? pio run -e {env} -t upload)")


def openocd(*commands: str) -> str:
    # CPUTAPID 0: don't stop on an IDCODE mismatch with clone chips.
    cmd = ["openocd", "-f", "interface/stlink.cfg", "-c", "set CPUTAPID 0",
           "-f", "target/stm32f1x.cfg", "-c", "init"]
    for c in commands:
        cmd += ["-c", c]
    cmd += ["-c", "shutdown"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    out = res.stdout + res.stderr
    if "open failed" in out or "init mode failed" in out:
        sys.exit("Could not connect to the ST-Link:\n" + out[-1500:])
    return out


def read_mem(addr: int, size: int) -> bytes:
    out = openocd(f"mdb {addr:#x} {size}")
    data = bytearray()
    for line in out.splitlines():
        m = re.match(r"^0x[0-9a-f]+:\s+((?:[0-9a-f]{2}\s*)+)$", line.strip())
        if m:
            data += bytes.fromhex(m.group(1).replace(" ", ""))
    if len(data) < size:
        sys.exit("openocd read failed:\n" + out[-1500:])
    return bytes(data[:size])
