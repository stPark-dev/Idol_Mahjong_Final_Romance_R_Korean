#!/usr/bin/env python3
"""조사용: Mednafen 세이브 스테이트(.mcN)에서 변수 하나 꺼내기.

변수 레코드: [이름 길이 1B][이름][크기 4B LE][데이터]. 같은 이름이 여러 섹션에 있으면 크기로 고른다.
usage: mcs.py get <state> <VAR> <out.bin> [size]
       mcs.py find <state> <VAR>
"""
import gzip, struct, sys


def find_all(d, name):
    key = bytes([len(name)]) + name.encode()
    i = 0
    while True:
        i = d.find(key, i)
        if i < 0:
            return
        sz = struct.unpack_from("<I", d, i + len(key))[0]
        yield i, sz, i + len(key) + 4
        i += 1


def get(path, name, size=None):
    d = gzip.open(path).read()
    for i, sz, off in find_all(d, name):
        if size is None or sz == size:
            return d[off:off + sz]
    raise KeyError(name)


if __name__ == "__main__":
    if sys.argv[1] == "find":
        d = gzip.open(sys.argv[2]).read()
        for i, sz, off in find_all(d, sys.argv[3]):
            print(hex(i), sz)
    else:
        size = int(sys.argv[5], 0) if len(sys.argv) > 5 else None
        open(sys.argv[4], "wb").write(get(sys.argv[2], sys.argv[3], size))
