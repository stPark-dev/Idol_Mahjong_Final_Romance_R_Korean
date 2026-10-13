/* PoC: replaces the slSynch call in the movie loop (literal 0x0600F5E4).
   r8 = CPK handle in the caller (0x0600F50C). Calls draw(handle), then tail-jumps to slSynch. */
    .section .text.entry,"ax"
    .global _start
_start:
    sts.l   pr,@-r15
    mov.l   1f,r0
    jsr     @r0
    mov     r8,r4
    lds.l   @r15+,pr
    mov.l   2f,r0
    jmp     @r0
    nop
    .align 2
1:  .long   draw
2:  .long   0x06004052
