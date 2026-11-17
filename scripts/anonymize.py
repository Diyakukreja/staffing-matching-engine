"""Produce a public-safe copy of candidates.json.

Usage:  python scripts/anonymize.py <raw candidates.json> <out candidates.json>

- Names are replaced with synthetic names. The mapping is one-to-one on the normalised
  name, so "same name, different person" cases (e.g. three people called the same thing)
  survive, as do the formatting quirks the cleaner must handle (ALL CAPS, double spaces).
- Phones are replaced with synthetic numbers. The mapping is one-to-one on the last 10 digits,
  so duplicate profiles (same person, phone typed differently) still collide, and the original
  formatting ("+91 xxxxx xxxxx", leading 0) is kept.
- Every other field (ids, localities, roles, pay, shifts, dates, education, status) is unchanged,
  so the matching results are identical to the raw data.
"""
import json
import random
import re
import sys

FIRST = ["Aarav", "Bhavna", "Chetan", "Deepika", "Eshwar", "Farida", "Gopal", "Hema", "Irfan", "Jaya",
         "Kishore", "Latha", "Manoj", "Nandini", "Omkar", "Pallavi", "Raghu", "Savitha", "Tarun", "Uma",
         "Vasanth", "Wasim", "Yamuna", "Zubair", "Anil", "Bindu", "Charan", "Divakar", "Ganga", "Hari"]
LAST = ["Alva", "Bhat", "Chavan", "Desai", "Gowder", "Jain", "Kamath", "Lobo", "Mendon", "Nair",
        "Pai", "Quadri", "Rai", "Salian", "Thomas", "Urs", "Vaz", "Wadiyar", "Yadav", "Zaidi"]


def _norm_name(n):
    return " ".join(str(n).split()).casefold()


def _digits(p):
    return re.sub(r"\D", "", str(p or ""))[-10:]


def anonymize(rows, seed=2026):
    rng = random.Random(seed)
    combos = [f"{f} {l}" for f in FIRST for l in LAST]
    rng.shuffle(combos)
    names = sorted({_norm_name(r["name"]) for r in rows})
    name_map = dict(zip(names, combos))                       # one-to-one
    phones = sorted({_digits(r["phone"]) for r in rows})
    fake = rng.sample(range(10_000_000, 99_999_999), len(phones))
    phone_map = {p: f"70{n:08d}" for p, n in zip(phones, fake)}  # one-to-one, obviously synthetic

    out = []
    for r in rows:
        r = dict(r)
        raw_name = str(r["name"])
        new = name_map[_norm_name(raw_name)]
        if raw_name.isupper():
            new = new.upper()
        if "  " in raw_name:
            new = new.replace(" ", "  ", 1)
        r["name"] = new
        raw_phone = str(r["phone"])
        p = phone_map[_digits(raw_phone)]
        if raw_phone.startswith("+91"):
            p = f"+91 {p[:5]} {p[5:]}"
        elif raw_phone.startswith("0"):
            p = "0" + p
        r["phone"] = p
        out.append(r)
    return out


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    rows = json.load(open(src, encoding="utf-8"))
    json.dump(anonymize(rows), open(dst, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"wrote {len(rows)} anonymised candidates to {dst}")
