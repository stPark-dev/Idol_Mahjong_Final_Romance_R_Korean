#!/bin/sh
set -e
cd "$(dirname "$0")"
F="-m4-nofpu -mb -Os -ffreestanding -fno-builtin -nostdlib -fno-pic -fno-zero-initialized-in-bss -Wall -Wextra -Werror"
sh4-linux-gnu-gcc $F -c crt.S -o /tmp/crt.o
sh4-linux-gnu-gcc $F -c poc.c -o /tmp/poc.o
sh4-linux-gnu-ld -EB --no-warn-rwx-segments -z noexecstack -T poc.ld /tmp/crt.o /tmp/poc.o -o /tmp/poc.elf
sh4-linux-gnu-objdump -d /tmp/poc.elf > poc.lst
python3 check_sh2.py poc.lst
sh4-linux-gnu-objcopy -O binary /tmp/poc.elf poc.bin
sh4-linux-gnu-nm -n /tmp/poc.elf | grep -E " [TtBbDd] "
ls -l poc.bin
