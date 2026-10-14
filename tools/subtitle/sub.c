/* Movie subtitles for Idol Mahjong Final Romance R (Saturn, 1ST.BIN V1.000).
 *
 * sub_prepare(): at the start of a movie sequence, before the game opens its CD stream,
 *   loads the pack of the movie-table row the sequence will play (ZSUB<row>.DAT, one
 *   character) straight into VDP1 VRAM at PACK_VRAM (idle while movies play), then runs the
 *   game's stream init. The row is worked out as the sequence 0x0600F360 does it.
 * sub_play(name): copies the cue table of that movie from VRAM, then plays the movie.
 * sub_frame(handle): once per displayed movie frame, registers the sprite of the cue whose
 *   [start, end) holds the movie clock (status block +0x58, 1/600 s).
 *
 * Pack (big-endian, 4-byte aligned): u32 MAGIC, u16 nmovies, u16 lut_srca, u32 total,
 *   nmovies x {char movie[4], u32 cue offset, u16 ncues, u16 0},
 *   cues {s32 start, s32 end, u16 srca, u16 size, s16 x, s16 y}, LUT, textures.
 */
typedef unsigned short u16;
typedef unsigned int u32;

#define GAME_LOAD    ((void (*)(const char *, void *, u32))0x0600DDB8)
#define GAME_PLAY    ((int (*)(const char *))0x0600F50C)
#define STREAM_INIT  ((void (*)(void))0x0600F604)
#define SET_SPRITE   ((int (*)(struct spr *, int))0x06005E20)
#define VDP1_VRAM    0x25C00000
#define PACK_VRAM    0x40000
#define VRAM_LIMIT   0x7F000
#define MAGIC        0x46525331                   /* "FRS1" */
#define MAX_CUES     24
#define Z_POS        0x00640000                   /* in front of the movie sprite (Z 170) */
#define PACK         ((volatile const u32 *)(VDP1_VRAM + PACK_VRAM))
#define ROWS         5
/* game bytes, read through the cache-through alias (0x2xxxxxxx); the game writes them
   through the write-through cache, so RAM always holds the current value */
#define BYTE(a)      (*(volatile const unsigned char *)(a))
#define MODE         BYTE(0x260D57BC)              /* sequence kind, 0x0600F388 */
#define WIN_STEP     BYTE(0x260D75DE)              /* kind 0 (after a win), 0x0600F408 */
#define CHAR         BYTE(0x260D75E0)              /* row for kinds 0 and 2 */
#define ALBUM_CHAR   BYTE(0x260D00A7)              /* row + 1 for kind 3 (0x0600F48C) */

struct spr { u16 ctrl, link, pmod, colr, srca, size; short xa, ya, xb, yb, xc, yc, xd, yd; u16 grda, dummy; };
struct cue { int start, end; u16 srca, size; short x, y; };

/* pack size, filled in by the build (tools/fromance/subtitles.py) after the marker */
struct pack_size { char marker[8]; u32 size[ROWS]; };
__attribute__((used, section(".data.size"))) struct pack_size pack_size = { "FRPACKSZ", { 0 } };

static char pack_name[] = "ZSUB0.DAT";
static u32 size;

static struct cue cues[MAX_CUES];
static int ncues;
static int ready;
static u16 lut_srca;
static struct spr s;

/* table row of the movies the sequence will play; ROWS if none has subtitles */
static u32 movie_row(void)
{
    unsigned char r;
    switch (MODE) {
    case 0: r = (unsigned char)(CHAR - (WIN_STEP > 2)); break;
    case 2: r = CHAR; break;
    case 3: r = (unsigned char)(ALBUM_CHAR - 1); break;
    default: return ROWS;                          /* kind 1: openings only, no subtitles */
    }
    return r;
}

void sub_prepare(void)
{
    u32 row = movie_row();
    ready = 0;
    size = row < ROWS ? pack_size.size[row] : 0;
    if (size && PACK_VRAM + size <= VRAM_LIMIT) {
        pack_name[4] = (char)('0' + row);
        GAME_LOAD(pack_name, (void *)(VDP1_VRAM + PACK_VRAM), size);
        ready = PACK[0] == MAGIC && PACK[2] == size;
        lut_srca = (u16)(PACK[1] & 0xFFFF);
    }
    STREAM_INIT();
}

static void select_movie(const char *name)
{
    u32 n, i, k, w, off, cnt;
    ncues = 0;
    if (!ready)
        return;
    n = PACK[1] >> 16;
    w = (u32)(unsigned char)name[0] << 24 | (u32)(unsigned char)name[1] << 16 |
        (u32)(unsigned char)name[2] << 8 | (u32)(unsigned char)name[3];
    for (i = 0; i < n; i++) {
        if (PACK[3 + 3 * i] != w)
            continue;
        off = PACK[4 + 3 * i];
        cnt = PACK[5 + 3 * i] >> 16;
        if (cnt > MAX_CUES || off % 4 || off + cnt * 16 > size)
            return;
        for (k = 0; k < cnt; k++) {
            volatile const u32 *p = PACK + off / 4 + k * 4;
            u32 a = p[2], b = p[3];
            cues[k].start = (int)p[0];
            cues[k].end = (int)p[1];
            cues[k].srca = (u16)(a >> 16);
            cues[k].size = (u16)a;
            cues[k].x = (short)(b >> 16);
            cues[k].y = (short)b;
        }
        ncues = (int)cnt;
        return;
    }
}

int sub_play(const char *name)
{
    int r;
    select_movie(name);
    r = GAME_PLAY(name);
    ncues = 0;
    return r;
}

void sub_frame(const u32 *h)
{
    int t = *(volatile const int *)(*h + 0x58);
    int i;
    for (i = 0; i < ncues; i++) {
        const struct cue *c = &cues[i];
        if (t >= c->start && t < c->end) {
            s.ctrl = 0; s.link = 0; s.pmod = 0x88; s.colr = lut_srca;
            s.srca = c->srca; s.size = c->size; s.xa = c->x; s.ya = c->y;
            SET_SPRITE(&s, Z_POS);
        }
    }
}
