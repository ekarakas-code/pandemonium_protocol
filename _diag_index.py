"""Diagnostic: report what the pandemonium index actually contains.
Usage: python _diag_index.py <path-to-pandemonium.db>
Read-only; opens in a separate connection (WAL allows concurrent readers).
"""
import sqlite3, sys, os

db = sys.argv[1] if len(sys.argv) > 1 else r"D:\ERHAN_RANDEVU\randevum2\.pandemonium\pandemonium.db"
print(f"DB: {db}  exists={os.path.exists(db)}  size={os.path.getsize(db) if os.path.exists(db) else 0}")
con = sqlite3.connect(db)
con.row_factory = sqlite3.Row
cur = con.cursor()

tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
print("TABLES:", tables)

def cols(t):
    return [r["name"] for r in cur.execute(f"PRAGMA table_info({t})")]

for t in ("symbols", "chunks", "relationships", "files"):
    if t in tables:
        try:
            n = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"\n{t}: {n} rows; columns={cols(t)}")
        except Exception as e:
            print(f"\n{t}: error {e}")

# language breakdown — try common column homes
for t, c in (("symbols", "language"), ("chunks", "language"), ("files", "language")):
    if t in tables and c in cols(t):
        print(f"\n{t}.{c} breakdown:")
        for r in cur.execute(f"SELECT {c} AS lang, COUNT(*) AS n FROM {t} GROUP BY {c} ORDER BY n DESC"):
            print(f"   {r['lang']!r}: {r['n']}")

# relationships kind breakdown (call/import/inherit edges)
if "relationships" in tables:
    rc = cols("relationships")
    kindcol = next((k for k in ("kind", "rel_type", "type", "edge_type") if k in rc), None)
    if kindcol:
        print(f"\nrelationships.{kindcol} breakdown:")
        for r in cur.execute(f"SELECT {kindcol} AS k, COUNT(*) AS n FROM relationships GROUP BY {kindcol} ORDER BY n DESC"):
            print(f"   {r['k']!r}: {r['n']}")

# decl_ref population (Step 8 — C++ only; should be ~0 on a C# repo)
for t in ("symbols", "chunks"):
    if t in tables and "decl_ref" in cols(t):
        n = cur.execute(f"SELECT COUNT(*) FROM {t} WHERE decl_ref IS NOT NULL AND decl_ref != ''").fetchone()[0]
        print(f"\n{t}.decl_ref populated: {n}")

con.close()
