/* PoC subtitle draw: one VDP1 normal sprite per active cue, time from the CPK status block. */
typedef unsigned short u16;
typedef unsigned int u32;
struct spr { u16 ctrl, link, pmod, colr, srca, size; short xa, ya, xb, yb, xc, yc, xd, yd; u16 grda, dummy; };
struct cue { int start, end; u16 srca, size; short x, y; };
#include "cues.h"
#define SET_SPRITE ((int (*)(struct spr *, int))0x06005E20)
static struct spr s;
volatile int last_t;

void draw(u32 *h)
{
    int t = *(volatile int *)(*h + 0x58);
    int i;
    last_t = t;
    for (i = 0; i < NCUES; i++) {
        const struct cue *c = &cues[i];
        if (t >= c->start && t < c->end) {
            s.ctrl = 0; s.link = 0; s.pmod = 0x88; s.colr = LUT_ADDR / 8;
            s.srca = c->srca; s.size = c->size; s.xa = c->x; s.ya = c->y;
            SET_SPRITE(&s, Z_POS);
        }
    }
}
