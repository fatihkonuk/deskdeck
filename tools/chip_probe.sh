#!/usr/bin/env bash
# Reads the STM32's identity and flash size through an ST-Link.
#   ./tools/chip_probe.sh               read only (does not halt the target)
#   ./tools/chip_probe.sh --flash-test  writes a test pattern above 64 KB and reads it back
#                                       (does a "C8" part actually have 128 KB?). Overwrites
#                                       the last 1 KB of the 128 KB range.
set -euo pipefail

# CPUTAPID 0: don't stop on an IDCODE mismatch with clone chips (CKS32, APM32...).
OCD=(openocd -f interface/stlink.cfg -c "set CPUTAPID 0" -f target/stm32f1x.cfg)

echo "== st-info --probe =="
st-info --probe

echo
echo "== Identity registers =="
"${OCD[@]}" -c "init" \
  -c "echo {DBGMCU_IDCODE (0xE0042000):}" -c "mdw 0xE0042000" \
  -c "echo {CPUID         (0xE000ED00):}" -c "mdw 0xE000ED00" \
  -c "echo {F_SIZE KB     (0x1FFFF7E0):}" -c "mdh 0x1FFFF7E0" \
  -c "echo {UID           (0x1FFFF7E8):}" -c "mdw 0x1FFFF7E8 3" \
  -c "shutdown" 2>&1 | grep -E '^(0x|DBGMCU|CPUID|F_SIZE|UID|Error|Warn)'

cat <<'EOF'

How to read this:
  DBGMCU_IDCODE low 12 bits 0x410 → medium-density F103 (C8/CB).
  CPUID 0x411FC231 → genuine STM32 (Cortex-M3 r1p1). 0x412FC231 → most likely a CKS32 clone.
  F_SIZE 0x0040 = 64 KB, 0x0080 = 128 KB.
EOF

if [[ "${1:-}" == "--flash-test" ]]; then
  tmp=$(mktemp -d)
  head -c 1024 /dev/urandom > "$tmp/pat.bin"
  echo
  echo "== Writing a 1 KB pattern at 0x0801FC00 (the 127th KB) =="
  st-flash --flash=128k write "$tmp/pat.bin" 0x0801FC00
  st-flash --flash=128k read "$tmp/back.bin" 0x0801FC00 1024
  if cmp -s "$tmp/pat.bin" "$tmp/back.bin"; then
    echo "RESULT: 128 KB of flash appears to be usable."
  else
    echo "RESULT: could not write/read above 64 KB → really 64 KB."
  fi
  rm -rf "$tmp"
fi
