"""Print aggregate checks for a private WeFlow JSON export, without chat text."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


LOCAL_TZ = timezone(timedelta(hours=8))


def audit(path: Path, start: str, end: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = payload["messages"]
    times = [int(row["createTime"]) for row in rows]
    local = [datetime.fromtimestamp(ts, LOCAL_TZ) for ts in times]
    ids = [str(row.get("localId")) for row in rows]
    fingerprints = [hashlib.sha256(json.dumps([
        row.get("createTime"), row.get("isSend"), row.get("content"),
        row.get("localType")], ensure_ascii=False).encode()).hexdigest()
        for row in rows]
    return {
        "count": len(rows),
        "source_returned": payload.get("coverage", {}).get("returned"),
        "source_may_have_more": payload.get("coverage", {}).get("mayHaveMore"),
        "first_local": min(local).isoformat() if local else None,
        "last_local": max(local).isoformat() if local else None,
        "by_month": dict(sorted(Counter(ts.strftime("%Y-%m") for ts in local).items())),
        "sender_direction": dict(Counter(str(row.get("isSend")) for row in rows)),
        "message_kind": dict(Counter(str(row.get("localType")) for row in rows)),
        "empty_text_field": sum(not (row.get("parsedContent") or row.get("content") or row.get("rawContent")) for row in rows),
        "duplicate_local_id": len(ids) - len(set(ids)),
        "duplicate_content_time_direction_kind": len(fingerprints) - len(set(fingerprints)),
        "increasing_timestamp_pairs": sum(a < b for a, b in zip(times, times[1:])),
        "decreasing_timestamp_pairs": sum(a > b for a, b in zip(times, times[1:])),
        "outside_local_date_range": sum(not start <= ts.date().isoformat() <= end for ts in local),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()
    print(json.dumps(audit(args.path, args.start, args.end), ensure_ascii=False, indent=2))
