"""두 기획서 JSON을 항목 단위로 비교한다.

    python compare.py expected.json output/planning.json

- [구조] 한쪽만 값이 있거나 비어 있음 / 목록 개수 다름  → 반드시 확인
- [문구] 둘 다 값이 있지만 문장이 다름                    → LLM 표현 차이일 수 있음(참고)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _empty(v) -> bool:
    return v is None or v == [] or v == ""


def diff(a, b, path=""):
    out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a.keys() | b.keys():
            out += diff(a.get(k), b.get(k), f"{path}.{k}" if path else k)
    elif isinstance(a, list) and isinstance(b, list) and (a and b):
        if len(a) != len(b):
            out.append(("구조", f"{path}[]", f"개수 {len(a)} ≠ {len(b)}"))
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{path}[{i}]")
    elif _empty(a) != _empty(b):
        out.append(("구조", path, f"기대={a!r} / 실제={b!r}"))
    elif a != b and not (_empty(a) and _empty(b)):
        out.append(("문구", path, f"기대={a!r} / 실제={b!r}"))
    return out


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    a, b = (json.loads(Path(p).read_text(encoding="utf-8")) for p in sys.argv[1:])
    d = sorted(diff(a, b))
    for kind, path, msg in d:
        print(f"[{kind}] {path}: {msg}")
    n = sum(1 for k, *_ in d if k == "구조")
    print(f"\n구조 차이 {n}개, 문구 차이 {len(d) - n}개")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
