"""Product build: source BIN/CUE -> patched track 1 + CUE, every change through a WritePlan."""
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import cdsector, iso9660, shopdesc, shopmark, shopmenu, shopmsg, subtitles
from .disc import SourceDisc, TRACK1_SHA1, parse_cue
from .writeplan import WritePlan

ROOT = Path(__file__).resolve().parents[2]
SUB_BIN = ROOT / "assets/subtitle/SUB.BIN"
VOICE_DIR = ROOT / "translation/voice"
SHOP_MENU = ROOT / "translation/graphics/shop_menu.json"
SHOP_DESC = ROOT / "translation/graphics/shop_desc.json"
SHOP_MSG = ROOT / "translation/graphics/shop_msg.json"
OUT_NAME = "Idol Mahjong - Final Romance R (Korean)"
OUT_CUE = f"{OUT_NAME}.cue"


class BuildError(RuntimeError):
    pass


def _file_sectors(name: str, lba: int, data: bytes, read) -> dict[int, bytes]:
    out = {}
    for i in range((len(data) + 2047) // 2048):
        chunk = data[i * 2048:(i + 1) * 2048]
        out[lba + i] = chunk + read(lba + i)[len(chunk):] if len(chunk) < 2048 else chunk
    return out


def _merge(writes: dict, secs: dict, name: str) -> None:
    for lba, user in secs.items():
        if lba in writes:
            raise BuildError(f"{name}: LBA {lba} already written")
        writes[lba] = user


def subtitle_parts(disc: SourceDisc, files: dict, text: str) -> tuple[bytes, dict[str, bytes]]:
    """New 1ST.BIN and {new file name: data}."""
    e = files["1ST.BIN"]
    first = disc.read_file(e.lba, e.size)
    packs = subtitles.build_packs(subtitles.voice_movies(VOICE_DIR, text=text), subtitles.movie_rows(first))
    sub = subtitles.fill_pack_sizes(SUB_BIN.read_bytes(), subtitles.pack_sizes(packs))
    return subtitles.patch_first(first, sub), packs


@dataclass(frozen=True)
class ShopMasks:
    write_sl: np.ndarray
    write_shop: np.ndarray
    keep_sl: np.ndarray          # bytes the module relies on and leaves alone
    keep_shop: np.ndarray


def shop_files(first: bytes, sl0: bytes, shop0: bytes) -> tuple[bytes, bytes]:
    """SL.BIN and SHOPDAT.BIN with every shop graphics text drawn from translation/graphics;
    every layout is read from the original SL.BIN."""
    sl, shop = shopmenu.build_files(first, sl0, shop0, shopmenu.load_texts(SHOP_MENU))
    sl, shop = shopmsg.build_files(first, sl, shop, shopmsg.load_texts(SHOP_MSG), base_sl=sl0)
    shop = shopdesc.build_file(first, sl0, shop, shopdesc.load_texts(SHOP_DESC))
    shop = shopmark.build_file(sl0, shop)
    return sl, shop


def shop_masks(first: bytes, sl0: bytes, shop0: bytes) -> list[ShopMasks]:
    """Write and keep masks of the menu, message, description and owned-mark modules."""
    n_sl, n_shop = len(sl0), len(shop0)
    menu, msg = shopmenu.Layout.read(first, sl0), shopmsg.Layout.read(first, sl0)
    offs = shopdesc.picture_offsets(sl0, n_shop)
    return [ShopMasks(*shopmenu.editable(menu, n_sl, n_shop), *shopmenu.kept(menu, first, n_sl, n_shop)),
            ShopMasks(*shopmsg.editable(msg, n_sl, n_shop), *shopmsg.kept(msg, first, n_sl, n_shop)),
            ShopMasks(np.zeros(n_sl, bool), shopdesc.editable(offs, n_shop), *shopdesc.kept(offs, n_sl, n_shop)),
            ShopMasks(np.zeros(n_sl, bool), shopmark.editable(shopmark.mark_offset(sl0, n_shop), n_shop),
                      *shopmark.kept(n_sl, n_shop))]


def check_shop(first: bytes, sl0: bytes, shop0: bytes, sl: bytes, shop: bytes) -> None:
    """Final shop files against the originals: no module writes a byte another module writes
    or relies on, no other byte changed, and every rewritten map entry or panel pixel is one
    the game can show."""
    if len(sl) != len(sl0) or len(shop) != len(shop0):
        raise BuildError("protected: shop file size changed")
    try:
        mods = shop_masks(first, sl0, shop0)
        for a in mods:
            for b in mods:
                if a is not b and ((a.write_sl & (b.write_sl | b.keep_sl)).any()
                                   or (a.write_shop & (b.write_shop | b.keep_shop)).any()):
                    raise BuildError("protected: a shop module may change bytes another module uses")
        sl_ok = np.logical_or.reduce([m.write_sl for m in mods])
        shop_ok = np.logical_or.reduce([m.write_shop for m in mods])
        for name, a, b, ok in (("SL.BIN", sl0, sl, sl_ok), ("SHOPDAT.BIN", shop0, shop, shop_ok)):
            stray = np.flatnonzero((np.frombuffer(a, np.uint8) != np.frombuffer(b, np.uint8)) & ~ok)
            if len(stray):
                raise BuildError(f"protected: {name} byte {int(stray[0]):#x} changed")
        shopmenu.check_targets(shopmenu.Layout.read(first, sl0), sl)
        shopmsg.check_targets(shopmsg.Layout.read(first, sl0), sl)
        shopdesc.check_colours(shopdesc.picture_offsets(sl0, len(shop0)), shop)
        shopmark.check_colours(shopmark.mark_offset(sl0, len(shop0)), shop)
    except (shopmenu.ShopMenuError, shopmsg.ShopMsgError, shopdesc.ShopDescError, shopmark.ShopMarkError) as ex:
        raise BuildError(str(ex)) from ex


def build(source_cue: Path, out_dir: Path, components: dict) -> dict:
    disc = SourceDisc(source_cue)
    try:
        return _build(disc, Path(source_cue), Path(out_dir), components)
    finally:
        disc.close()


def _build(disc: SourceDisc, source_cue: Path, out_dir: Path, components: dict) -> dict:
    disc.verify()
    files = iso9660.list_files(disc.user)
    writes: dict[int, bytes] = {}
    sizes: dict[str, int] = {}
    placed: list[tuple[str, int, int]] = []
    report: dict[str, dict] = {}
    text = components.get("subtitles")
    if text:
        first, added = subtitle_parts(disc, files, text)
        e = files["1ST.BIN"]
        if (len(first) + 2047) // 2048 != e.sectors:
            raise BuildError("1ST.BIN would need another sector")
        _merge(writes, _file_sectors("1ST.BIN", e.lba, first, disc.user), "1ST.BIN")
        sizes["1ST.BIN"] = len(first)
        report["1ST.BIN"] = {"old_size": e.size, "new_size": len(first)}
        lba = max(f.lba + f.sectors for f in files.values() if f.lba < disc.sectors)
        for name, data in added.items():
            n = (len(data) + 2047) // 2048
            if lba + n > disc.sectors:
                raise BuildError(f"no room for {name}: track 1 ends at LBA {disc.sectors}")
            _merge(writes, _file_sectors(name, lba, data.ljust(n * 2048, b"\0"), disc.user), name)
            placed.append((name, lba, len(data)))
            report[name] = {"lba": lba, "size": len(data)}
            lba += n
    if components.get("shop"):
        read = lambda n: disc.read_file(files[n].lba, files[n].size)
        first, sl0, shop0 = read("1ST.BIN"), read("SL.BIN"), read("SHOPDAT.BIN")
        try:
            sl, shop = shop_files(first, sl0, shop0)
            check_shop(first, sl0, shop0, sl, shop)
        except (shopmenu.ShopMenuError, shopmsg.ShopMsgError, shopdesc.ShopDescError, shopmark.ShopMarkError) as ex:
            raise BuildError(str(ex)) from ex
        for name, data in (("SL.BIN", sl), ("SHOPDAT.BIN", shop)):
            _merge(writes, _file_sectors(name, files[name].lba, data, disc.user), name)
            report[name] = {"replaced": True, "size": len(data)}
    if sizes or placed:
        try:
            _merge(writes, iso9660.repack_root(disc.user, sizes, placed), "root directory")
        except iso9660.IsoError as ex:
            raise BuildError(str(ex)) from ex

    plan = WritePlan(disc.bin)
    for lba, user in sorted(writes.items()):
        raw = disc.raw(lba)
        sec = bytearray(raw)
        sec[16:16 + 2048] = user
        cdsector.fix_mode1(sec)
        if bytes(sec) != raw:
            plan.add(f"lba{lba}", disc.offset + lba * cdsector.RAW, raw, bytes(sec))

    out_dir.mkdir(parents=True, exist_ok=True)
    cue_text = source_cue.read_text(encoding="utf-8", errors="replace")
    track1 = parse_cue(cue_text).file
    out_bin = out_dir / f"{OUT_NAME} (Track 01).bin"
    if out_bin.resolve() == disc.bin.resolve():
        raise BuildError("output would overwrite the source")
    plan.apply(out_bin)
    cue_out = cue_text.replace(f'"{track1}"', f'"{out_bin.name}"')
    for other in sorted(set(re.findall(r'FILE\s+"(.+)"\s+BINARY', cue_text)) - {track1}):
        src = source_cue.with_name(other)
        dst = out_dir / other.replace("(Japan)", "(Korean)")
        if dst.resolve() == src.resolve():
            raise BuildError(f"output {dst} would overwrite a source track")
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            try:
                if dst.exists():
                    dst.unlink()
                os.link(src, dst)
            except OSError:
                shutil.copyfile(src, dst)
        cue_out = cue_out.replace(f'"{other}"', f'"{dst.name}"')
    (out_dir / OUT_CUE).write_text(cue_out, encoding="utf-8")

    h = hashlib.sha1()
    with open(out_bin, "rb") as f:
        for b in iter(lambda: f.read(1 << 22), b""):
            h.update(b)
    manifest = {
        "source_track1_sha1": TRACK1_SHA1,
        "components": components,
        "subtitles": text,
        "files": report,
        "sectors_changed": len(plan.writes),
        "output_bin": out_bin.name,
        "output_sha1": h.hexdigest(),
        # release needs reviewed Korean subtitles and the rest of the game; never for test builds
        "distribution": False,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
