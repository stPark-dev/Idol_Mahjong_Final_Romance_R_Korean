#!/bin/sh
# Build assets/subtitle/SUB.BIN from crt.S + sub.c with SH-4 gcc 13+ (no -m2; SH-2 check below).
# gcc 12.2 miscompiles "if (x)" on loaded values (sh_treg_combine) and is refused.
#   docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:/w" -w /w khsub sh tools/subtitle/build.sh
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${OUT:-$HERE/../../assets/subtitle/SUB.BIN}"
TMP="${TMPDIR:-/tmp}/frsub"
mkdir -p "$TMP"
CC=${CC:-sh4-linux-gnu-gcc}
PFX=${CC%gcc}
if [ "$($CC -dumpversion | cut -d. -f1)" -lt 13 ]; then
    echo "gcc $($CC -dumpversion) miscompiles SH code; use gcc 13+ (khsub image)" >&2; exit 1
fi
FLAGS="-m4-nofpu -mb -Os -ffreestanding -fno-builtin -nostdlib -fno-pic -fno-zero-initialized-in-bss \
       -fno-tree-loop-distribute-patterns -Wall -Wextra -Werror"
$CC $FLAGS -c "$HERE/crt.S" -o "$TMP/crt.o"
$CC $FLAGS -c "$HERE/sub.c" -o "$TMP/sub.o"
${PFX}ld -EB --no-warn-rwx-segments -z noexecstack -T "$HERE/sub.ld" "$TMP/crt.o" "$TMP/sub.o" -o "$TMP/sub.elf"
${PFX}objdump -d "$TMP/sub.elf" > "$TMP/sub.lst"
python3 "$HERE/check_sh2.py" "$TMP/sub.lst"
${PFX}objcopy -O binary -j .text -j .rodata -j .data "$TMP/sub.elf" "$OUT"
${PFX}nm -n "$TMP/sub.elf" | grep -E " [TtDdBb] "
ls -l "$OUT"
