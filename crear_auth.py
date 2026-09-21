"""
Generate config/auth.yaml from the users in the database.

    python crear_auth.py

Passwords are stored ONLY as PBKDF2 hashes with a per-user salt. The YAML
never contains a plaintext password.

For the demo every user shares one password, printed once here. In
production this would be an activation email; it is an explicit scope call.
"""

import os
import sys

import auth
import db

conn = db.conectar()

total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
if total == 0:
    print("No users in the database. Run this first:  python etl.py")
    sys.exit(1)

password = os.environ.get("DEMO_PASSWORD", "profood2026")
created, password = auth.sembrar_credenciales(conn, password)

print(f"config/auth.yaml written · {total} users ({created} new)\n")
print("  Password for every account (demo only):", password)
print("  Stored as PBKDF2-SHA256 with a per-user salt.\n")

print("  One account per role, to test permissions:\n")
rows = conn.execute(
    "SELECT name, email, role, team FROM users "
    "WHERE role IN ('admin','lead','analyst','viewer') "
    "GROUP BY role ORDER BY CASE role "
    "WHEN 'admin' THEN 1 WHEN 'lead' THEN 2 "
    "WHEN 'analyst' THEN 3 ELSE 4 END"
).fetchall()
for r in rows:
    print(f"    {r['role']:<8} {r['email']:<34} {r['name']} · {r['team']}")

print("\n  Remember config/auth.yaml belongs in .gitignore.")