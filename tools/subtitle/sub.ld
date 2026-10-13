OUTPUT_FORMAT("elf32-shbig-linux")
ENTRY(_start)
SECTIONS
{
    . = 0x06058078;
    .text : { *(.text.entry) *(.text .text.*) }
    .rodata : { *(.rodata .rodata.*) }
    .data : { *(.data .data.*) }
    _image_end = .;
    .bss : { *(.bss .bss.*) *(COMMON) }
    _end = .;
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
/* 1ST.BIN's last sector has 1,928 spare bytes; free RAM runs to the backup library at 0x0605B000 */
ASSERT(_image_end <= 0x06058078 + 1928, "SUB.BIN does not fit in the last sector of 1ST.BIN")
ASSERT(_end <= 0x0605B000, "subtitle BSS overruns the backup library area")
