import sqlite3
conn = sqlite3.connect("job_bot.db")
rows = conn.execute(
    "SELECT created_at, level, module, message FROM logs "
    "ORDER BY created_at DESC LIMIT 15"
).fetchall()
for r in rows:
    print(r[0], r[1], r[2], r[3][:120])
conn.close()
