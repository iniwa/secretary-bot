"""実機 DB の recommended_team_notes / recommended_notes / skill_summary を可視化。"""

import sqlite3
import sys

DB = "/app/data/bot.db"

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute(
    "SELECT id, name_ja, recommended_team_notes, recommended_notes, skill_summary "
    "FROM zzz_characters ORDER BY display_order, id"
)
for r in cur.fetchall():
    tn = r["recommended_team_notes"]
    rn = r["recommended_notes"]
    ss = r["skill_summary"]
    tn_len = len(tn) if tn else 0
    rn_len = len(rn) if rn else 0
    ss_len = len(ss) if ss else 0
    if len(sys.argv) > 1 and sys.argv[1] == "--details":
        print(f"[{r['id']}] {r['name_ja']}  tn={tn_len} rn={rn_len} ss={ss_len}")
        if tn:
            print(f"  team_notes: {tn[:120]!r}")
        if rn:
            print(f"  notes:      {rn[:120]!r}")
        if ss:
            print(f"  summary:    {ss[:120]!r}")
    else:
        print(f"[{r['id']:3d}] {r['name_ja']:24s}  tn={tn_len:4d}  rn={rn_len:4d}  ss={ss_len:4d}")
