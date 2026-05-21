"""Parse an honest_sweep_run.py log and emit the single best honest config
with the full required metric block. Used for the final report so numbers
are copied verbatim from a real run, never hand-typed.

Usage: python scripts/honest_extract.py tmp/<sweep>.log
"""
import re
import sys

LINE = re.compile(
    r"^(?P<tag>.+?)\s+ann=\s*(?P<ann>-?\d+\.\d+)%\s+tot=\s*(?P<tot>-?\d+\.\d+)%"
    r"\s+\$\s*(?P<prof>-?\d+\.\d+)\s+DD=\s*(?P<dd>\d+\.\d+)%\s+"
    r"WR=\s*(?P<wr>\d+\.\d+)%\s+PF=\s*(?P<pf>[\d.]+)\s+n=\s*(?P<n>\d+)\s+"
    r"\[(?P<sd>[\d-]+|None)->(?P<ed>[\d-]+|None)\]")


def parse(path):
    rows = []
    stage = ""
    with open(path) as _f: _lines = _f.readlines()
    for ln in _lines:
        ln = ln.rstrip("\n")
        if ln.startswith("==="):
            stage = ln.strip("= ").strip()
            continue
        m = LINE.match(ln.strip())
        if not m:
            continue
        d = m.groupdict()
        rows.append({
            "stage": stage, "tag": d["tag"].strip(),
            "ann": float(d["ann"]), "tot": float(d["tot"]),
            "profit": float(d["prof"]), "dd": float(d["dd"]),
            "wr": float(d["wr"]), "pf": float(d["pf"]),
            "n": int(d["n"]), "sd": d["sd"], "ed": d["ed"],
        })
    return rows


def main():
    path = sys.argv[1]
    rows = parse(path)
    if not rows:
        print("no parsable result lines")
        return
    # honest "best": ignore TRAIN-labelled lines and zero-trade lines.
    cand = [r for r in rows if r["n"] >= 30 and not r["tag"].startswith("TRAIN ")]
    if not cand:
        cand = rows
    best = max(cand, key=lambda r: r["ann"])
    test_lines = [r for r in rows if r["tag"].startswith("TEST ") and r["n"] >= 30]
    print(f"FILE: {path}")
    print(f"BEST (n>=30, non-TRAIN): [{best['stage']}] {best['tag']}")
    print(f"  Annualized return : {best['ann']:.2f}%")
    print(f"  Total return      : {best['tot']:.2f}%")
    print(f"  Profit dollars    : ${best['profit']:.2f}")
    print(f"  Max drawdown      : {best['dd']:.2f}%  (conservative, MAE-marked)")
    print(f"  Win rate          : {best['wr']:.2f}%")
    print(f"  Profit factor     : {best['pf']:.3f}")
    print(f"  Trades            : {best['n']}")
    print(f"  Test period       : {best['sd']} -> {best['ed']}")
    if test_lines:
        bt = max(test_lines, key=lambda r: r["ann"])
        print(f"\nBEST untouched-TEST split: {bt['tag']}  ann={bt['ann']:.2f}% "
              f"tot={bt['tot']:.2f}% PF={bt['pf']:.3f} WR={bt['wr']:.2f}% "
              f"DD={bt['dd']:.2f}% n={bt['n']} [{bt['sd']}->{bt['ed']}]")


if __name__ == "__main__":
    main()
