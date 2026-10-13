OUTPUT_FORMAT("elf32-shbig-linux")
ENTRY(_start)
SECTIONS
{
    . = 0x06058100;
    .text : { *(.text.entry) *(.text .text.*) }
    .rodata : { *(.rodata .rodata.*) }
    .data : { *(.data .data.*) }
    .bss : { *(.bss .bss.*) *(COMMON) }
    _end = .;
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
ASSERT(_end <= 0x0605B000, "PoC overruns free RAM")
