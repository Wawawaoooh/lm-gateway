"""按 API Key 汇总 token 用量（只读查询 gateway.db）。"""
import sqlite3

conn = sqlite3.connect(r"file:f:/Peterpertrilli/gateway.db?mode=ro", uri=True)
conn.execute("PRAGMA busy_timeout=3000")
rows = conn.execute(
    """SELECT k.id, k.name, k.prefix,
              COUNT(*) AS reqs,
              SUM(r.prompt_tokens) AS ptok,
              SUM(r.completion_tokens) AS ctok,
              SUM(r.prompt_tokens + r.completion_tokens) AS total
       FROM keys k LEFT JOIN requests r ON r.key_id = k.id
       GROUP BY k.id ORDER BY total DESC"""
).fetchall()

header = f"{'ID':<4} {'名称':<16} {'前缀':<14} {'请求数':>7} {'Prompt':>10} {'Completion':>11} {'总Token':>10}"
print(header)
print("-" * len(header))
for r in rows:
    print(f"{r[0]:<4} {str(r[1]):<16} {str(r[2]):<14} {r[3] or 0:>7} "
          f"{r[4] or 0:>10,} {r[5] or 0:>11,} {r[6] or 0:>10,}")
conn.close()
