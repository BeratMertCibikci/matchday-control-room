"""CLI: generate a synthetic match dataset.

    python -m mcr.sim --seed 5 --out data/match_5.jsonl [--frames]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mcr.sim.engine import generate_match


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a synthetic football match as JSONL")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--frames", action="store_true", help="include 22-player positional frames")
    args = ap.parse_args()
    meta, events = generate_match(args.seed)
    lines = [json.dumps({"meta": meta.__dict__})] + [json.dumps(e.to_dict(with_frame=args.frames)) for e in events]
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text("\n".join(lines) + "\n")
        print(f"wrote {len(events)} events to {args.out}")
    else:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
