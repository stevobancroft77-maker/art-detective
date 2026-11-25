import os
import json

def connect():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10)

def initialize(seed):
    with connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS ad_cases (name text PRIMARY KEY, payload jsonb NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS ad_works (id text PRIMARY KEY, payload jsonb NOT NULL)")
        for row in seed:
            conn.execute("INSERT INTO ad_works VALUES (%s,%s::jsonb) ON CONFLICT DO NOTHING",(row["id"],json.dumps(row)))
        cases=dict(conn.execute("SELECT name,payload FROM ad_cases").fetchall())
        for name, case in cases.items():
            if (case.get("run") or {}).get("status")=="running":
                case["run"]["status"]="interrupted"
                conn.execute("UPDATE ad_cases SET payload=%s::jsonb WHERE name=%s",(json.dumps(case),name))
        return cases

def save(name,payload):
    with connect() as conn:
        conn.execute("INSERT INTO ad_cases VALUES (%s,%s::jsonb) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",(name,json.dumps(payload)))

def works():
    with connect() as conn:
        return [r[0] for r in conn.execute("SELECT payload FROM ad_works ORDER BY id")]

def import_works(rows):
    with connect() as conn:
        count=0
        for row in rows:
            count+=conn.execute("INSERT INTO ad_works VALUES (%s,%s::jsonb) ON CONFLICT DO NOTHING",(row["id"],json.dumps(row))).rowcount
        return count
