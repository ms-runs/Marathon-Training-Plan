#!/usr/bin/env python3
"""Rewrite sub3_2026_2027_live.ics from the Daily Desk `plan` collection.

    python3 tools/plan_to_ics.py --plan plan.jsonl [--settings settings.json] [--ics sub3_2026_2027_live.ics]
                                 [--today YYYY-MM-DD] [--check]

--plan      the `plan` collection: a folder of .json files, a .jsonl file (one doc per line, as db_list prints
            them), or a JSON array. Each doc is {"id","data":{...},"updatedAt"} or the bare data object.
--settings  the `coach/settings` doc (its `bRaces` become race events). Optional.
--check     print what would change and exit 1 if the file is out of date; write nothing.

Rules
- The file head (everything before the first VEVENT) is kept as is.
- Every plan doc owns its date: any existing event on that date is replaced by what the plan says now
  (a run event if km > 0 or it is a race, plus a strength event if the title mentions strength).
  Skipped days get no event. Plain rest days get no event.
- Legacy template events (random UIDs) dated before the first plan day are kept byte for byte; from that day on
  the plan supersedes them.
- Coach events (run-/str-/race-YYYYMMDD@ms-runs) with no plan doc: kept if in the past, dropped if today or later
  (the plan no longer has that day). Race events for coach/settings.bRaces are (re)generated.
- UIDs are stable, and DTSTAMP comes from the plan doc's updatedAt, so running this twice gives identical bytes:
  the script only changes the file when the plan really changed.
- The repo is public, so the plan's free-text `why` / `reason` notes (which can name people or places) are left out
  of descriptions. Pass --notes to include them.
"""
import argparse, datetime as dt, glob, json, os, re, sys
from zoneinfo import ZoneInfo

CRLF = "\r\n"
MANAGED = re.compile(r"^(run|str|race)-(\d{8})@ms-runs$")


def fold(line):
    """RFC 5545: lines over 75 octets continue on the next line after a space."""
    out, b = [], line.encode("utf-8")
    while len(b) > 75:
        cut = 75 if not out else 74
        while cut > 0 and (b[cut] & 0xC0) == 0x80:  # never split a UTF-8 character
            cut -= 1
        out.append(b[:cut].decode("utf-8"))
        b = b[cut:]
    out.append(b.decode("utf-8"))
    return (CRLF + " ").join(out)


def esc(t):
    return str(t).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def event(uid, date, summary, desc, stamp):
    d = dt.date.fromisoformat(date)
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}",
             f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + dt.timedelta(days=1):%Y%m%d}",
             f"SUMMARY:{esc(summary)}", f"DESCRIPTION:{esc(desc)}", "TRANSP:TRANSPARENT", "END:VEVENT"]
    return "".join(fold(l) + CRLF for l in lines)


def split_ics(text):
    i = text.index("BEGIN:VEVENT")
    return text[:i], re.findall(r"BEGIN:VEVENT\r?\n.*?END:VEVENT\r?\n", text, re.S)


def block_field(b, name):
    m = re.search(r"^" + name + r"[^:\r\n]*:(.*)$", b, re.M)
    return m.group(1).strip() if m else None


def block_date(b):
    v = block_field(b, "DTSTART")
    return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if v and re.match(r"\d{8}", v) else None


def to_stamp(*cands):
    """Newest of the given ISO timestamps, as UTC YYYYMMDDTHHMMSSZ."""
    best = None
    for c in cands:
        if not c:
            continue
        try:
            t = dt.datetime.fromisoformat(str(c).replace("Z", "+00:00"))
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        t = t.astimezone(dt.timezone.utc)
        best = t if best is None or t > best else best
    return (best or dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def docs_from(path):
    """Yield dicts from a folder, a .jsonl file or a JSON array. Tool wrapper lines are skipped."""
    items = []
    if os.path.isdir(path):
        for f in sorted(glob.glob(os.path.join(path, "*.json"))):
            items.append(json.load(open(f, encoding="utf-8")))
    else:
        raw = open(path, encoding="utf-8").read().strip()
        try:
            v = json.loads(raw)
            items = v if isinstance(v, list) else [v]
        except json.JSONDecodeError:
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return items


def load_plan(path):
    plan = {}
    for d in docs_from(path):
        data = d.get("data", d)
        k = data.get("date")
        if not (isinstance(k, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", k)):
            continue
        plan[k] = (data, to_stamp(d.get("updatedAt"), data.get("updatedAt")))
    return plan


def load_settings(path):
    if not path:
        return None, None
    docs = docs_from(path)
    if not docs:
        return None, None
    d = docs[0]
    return d.get("data", d), to_stamp(d.get("updatedAt"), d.get("data", d).get("updatedAt"))


def num(x):
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


def plan_events(plan, notes):
    out = []  # (date, uid, summary, desc, stamp)
    for k in sorted(plan):
        x, stamp = plan[k]
        if x.get("status") == "skipped":
            continue
        ymd = k.replace("-", "")
        title = str(x.get("title") or "")
        if (x.get("km") or 0) > 0 or x.get("type") == "race":
            desc = []
            if x.get("pace"):
                desc.append(str(x["pace"]))
            if x.get("purpose"):
                desc.append(str(x["purpose"]))
            if notes:
                if x.get("reason"):
                    desc.append("Change: " + str(x["reason"]))
                elif x.get("why"):
                    desc.append("Note: " + str(x["why"]))
            desc.append(f"Planned by your Daily Desk coach, {stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}.")
            out.append((k, f"run-{ymd}@ms-runs", title, "\n\n".join(desc), stamp))
        if "strength" in title.lower():
            out.append((k, f"str-{ymd}@ms-runs", "Strength Training (20 min)",
                        "• Split squats\n• RDLs\n• Calf raises\n• Core circuit\n\nPurpose: Injury resistance & power", stamp))
    return out


def race_events(settings, sstamp, plan):
    out = []
    for b in (settings or {}).get("bRaces", []):
        d = b.get("date")
        if not d or d in plan:
            continue
        desc = f"{num(b.get('km', ''))} km"
        if b.get("climb"):
            desc += f", about {b['climb']} m of climb"
        if b.get("goalTime") and b.get("goalPace"):
            desc += f". Goal: {b['goalTime']} ({b['goalPace'] // 60}:{b['goalPace'] % 60:02d}/km)"
        out.append((d, f"race-{d.replace('-', '')}@ms-runs", "RACE: " + b["name"], desc + ".", sstamp))
    return out


def build(raw, plan, settings, sstamp, today, notes):
    head, blocks = split_ics(raw)
    first = min(plan) if plan else "9999-12-31"
    race_dates = {b.get("date") for b in (settings or {}).get("bRaces", [])} if settings else set()
    keep = []
    for b in blocks:
        d, uid = block_date(b), block_field(b, "UID") or ""
        managed = MANAGED.match(uid)
        if d is None:
            keep.append(b)
        elif d in plan:
            continue                                   # the plan owns this day
        elif managed and managed.group(1) == "race" and settings is not None and d in race_dates:
            continue                                   # regenerated from settings
        elif managed and d >= today:
            continue                                   # coach event the plan no longer has
        elif not managed and d >= first:
            continue                                   # old template, superseded by the coach plan
        else:
            keep.append(b)
    new = plan_events(plan, notes)
    # races from settings; if settings weren't supplied, existing race events were kept above
    new += race_events(settings, sstamp, plan)
    new.sort(key=lambda e: (e[0], e[1]))
    body = head + "".join(keep) + "".join(event(u, d, s, t, st) for d, u, s, t, st in new) + "END:VCALENDAR" + CRLF
    return body, len(keep), new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--settings")
    ap.add_argument("--ics", default="sub3_2026_2027_live.ics")
    ap.add_argument("--today", default=dt.datetime.now(ZoneInfo("Europe/London")).date().isoformat())
    ap.add_argument("--notes", action="store_true", help="include the plan's why/reason notes in descriptions")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    plan = load_plan(a.plan)
    if not plan:
        sys.exit("no plan docs found in " + a.plan + ": refusing to rewrite the calendar from nothing")
    settings, sstamp = load_settings(a.settings)
    raw = open(a.ics, encoding="utf-8", newline="").read()
    body, kept, new = build(raw, plan, settings, sstamp, a.today, a.notes)
    changed = body != raw
    print(f"plan days: {len(plan)} ({min(plan)} to {max(plan)}); kept {kept} earlier events; wrote {len(new)} coach events")
    print("file would change" if changed else "file already up to date")
    if changed and not a.check:
        with open(a.ics, "w", encoding="utf-8", newline="") as f:
            f.write(body)
        print("wrote", a.ics)
    sys.exit(1 if (changed and a.check) else 0)


if __name__ == "__main__":
    main()
