/* Entry points of SUB.BIN, appended to 1ST.BIN at 0x06058078.
   +0x00 play:    replaces the play-movie literal 0x0600F504 (r4 = movie file name).
   +0x08 frame:   replaces the slSynch literal 0x0600F5E4 inside the movie loop
                  (r8 = CPK handle of the caller); tail-jumps to slSynch.
   +0x18 prepare: replaces the stream-init literal 0x0600F3E0 of the movie sequence
                  (0x0600F360); loads the subtitle pack while the CD is still idle. */
    .section .text.entry,"ax"
    .global _start
_start:
    mov.l   1f,r0
    jmp     @r0
    nop
    nop
    sts.l   pr,@-r15
    mov.l   2f,r0
    jsr     @r0
    mov     r8,r4
    lds.l   @r15+,pr
    mov.l   3f,r0
    jmp     @r0
    nop
    mov.l   4f,r0
    jmp     @r0
    nop
    nop
    .align 2
1:  .long   sub_play
2:  .long   sub_frame
3:  .long   0x06004052
4:  .long   sub_prepare
