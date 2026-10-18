"""usdata: crawl | wayback | add | build [--check]

Run from the repo root:  python -m usdata <command>   (with PYTHONPATH=pipeline, or `cd pipeline`)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import archive


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="usdata")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("crawl", help="download new files from the open USCIS data listing into archive/")
    a = sub.add_parser("add", help="archive files a human downloaded (e.g. State Dept, which blocks crawlers)")
    a.add_argument("paths", nargs="+", type=Path)
    a.add_argument("--dataset", required=True, choices=["uscis-forms", "uscis-i130-awaiting", "state-iv"])
    a.add_argument("--url", help="where the file was downloaded from (defaults to the dataset's listing page)")
    w = sub.add_parser("wayback", help="backfill files the publisher removed, from the Internet Archive")
    w.add_argument("--dataset", required=True, choices=["uscis-forms", "uscis-i130-awaiting", "state-iv"])
    b = sub.add_parser("build", help="archive/ + catalog/ -> site/public/data/v1/")
    b.add_argument("--check", action="store_true", help="fail if committed output differs from a fresh build")
    args = p.parse_args(argv)

    if args.cmd == "crawl":
        from .crawl import crawl
        r = crawl()
        print(f"added {len(r.added)} file(s), {r.skipped_known} already archived, "
              f"{len(r.unclassified)} links not in a tracked dataset")
        for path in r.added:
            print("  +", path)
        return 0

    if args.cmd == "wayback":
        from .wayback import backfill
        r = backfill(args.dataset)
        print(f"added {len(r.added)}, already held {r.known}, failed {len(r.failed)}")
        for path in r.added:
            print("  +", path)
        for url in r.failed:
            print("  ! failed", url)
        return 0

    if args.cmd == "add":
        from .datasets import DATASETS
        spec = DATASETS[args.dataset]
        files = [f for p_ in args.paths for f in (sorted(p_.iterdir()) if p_.is_dir() else [p_])
                 if f.suffix.lower() in (".xlsx", ".csv")]
        for f in files:
            url = args.url or f"{spec.listing}#{f.name}"
            sf, created = archive.add(f.read_bytes(), dataset=spec.id, url=url, original_name=f.name,
                                      retrieved_by="human")
            print(("+ " if created else "= ") + sf.archive_path)
        return 0

    if args.cmd == "build":
        from .build import build
        from .publish import DATA, write, differs
        ledger, checks = build()
        errors = [c for c in checks if c.severity == "error"]
        for c in checks:
            if c.severity != "info":
                print(f"[{c.severity}] {c.check}: {c.detail}")
        if errors:
            print(f"{len(errors)} error(s); nothing written", file=sys.stderr)
            return 1
        if args.check:
            if differs(ledger, checks, DATA):
                print("site/public/data/v1 is stale: run `python -m usdata build` and commit", file=sys.stderr)
                return 1
            print("data/v1 is up to date")
            return 0
        write(ledger, checks, DATA)
        print(f"{len(ledger.facts):,} facts, {len(ledger.series):,} series, {len(ledger.sources)} sources "
              f"-> {DATA.relative_to(archive.REPO).as_posix()}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
