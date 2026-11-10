"""SQL equivalents of every check in compare_graphs.py, run on MySQL fraud_1_1m and compared with Neo4j."""
import os, subprocess, sys, time
from compare_graphs import CHECKS, neo4j

TX = "CAST(SUBSTRING({v}, 4) AS UNSIGNED)"                       # 'tx-123' -> 123
CID = "(CAST({v} AS UNSIGNED) % 1000003)"
DIG = "(CAST(REPLACE(REPLACE({v}, '-', ''), 'B', '') AS UNSIGNED) % 1000003)"
def tx(v): return TX.format(v=v)
def cid(v): return CID.format(v=v)
def dig(v): return DIG.format(v=v)

def per_type(t):
    return (f"SELECT COUNT(*), SUM(fraud), CAST(SUM(amount * 100) AS SIGNED), SUM(step), SUM(global_step), SUM(ts), "
            f"SUM({tx('id')}) FROM transactions WHERE type = '{t}'")

SQL = [
    # total nodes
    "SELECT (SELECT COUNT(*) FROM banks) + (SELECT COUNT(*) FROM merchants) + (SELECT COUNT(*) FROM ssns) + "
    "(SELECT COUNT(*) FROM emails) + (SELECT COUNT(*) FROM phones) + (SELECT COUNT(*) FROM clients) + "
    "(SELECT COUNT(*) FROM transactions)",
    # total relationships (only edges whose far end exists, as in a graph)
    "SELECT (SELECT COUNT(*) FROM transactions t JOIN clients c ON c.id = t.client_id)"
    " + (SELECT COUNT(*) FROM transactions t JOIN merchants m ON m.id = t.to_merchant_id)"
    " + (SELECT COUNT(*) FROM transactions t JOIN clients c ON c.id = t.to_client_id)"
    " + (SELECT COUNT(*) FROM transactions t JOIN banks b ON b.id = t.to_bank_id)"
    " + (SELECT COUNT(*) FROM transactions a JOIN transactions b ON b.id = a.next_transaction_id)"
    " + (SELECT COUNT(*) FROM clients c JOIN transactions t ON t.id = c.first_tx_id)"
    " + (SELECT COUNT(*) FROM clients c JOIN transactions t ON t.id = c.last_tx_id)"
    " + (SELECT COUNT(*) FROM clients c JOIN ssns s ON s.ssn = c.ssn)"
    " + (SELECT COUNT(*) FROM clients c JOIN emails e ON e.email = c.email)"
    " + (SELECT COUNT(*) FROM clients c JOIN phones p ON p.phone_number = c.phone_number)",
    # nodes per label
    "SELECT l, c FROM (SELECT 'Bank' l, COUNT(*) c FROM banks UNION ALL "
    "SELECT type, COUNT(*) FROM transactions GROUP BY type UNION ALL "
    "SELECT 'Client', COUNT(*) FROM clients UNION ALL SELECT 'Email', COUNT(*) FROM emails UNION ALL "
    "SELECT 'Merchant', COUNT(*) FROM merchants UNION ALL SELECT 'Mule', SUM(is_mule) FROM clients UNION ALL "
    "SELECT 'Phone', COUNT(*) FROM phones UNION ALL SELECT 'SSN', COUNT(*) FROM ssns UNION ALL "
    "SELECT 'Transaction', COUNT(*) FROM transactions) x ORDER BY CAST(l AS BINARY)",
    # labels per node
    "SELECT 1, (SELECT COUNT(*) FROM banks) + (SELECT COUNT(*) FROM merchants) + (SELECT COUNT(*) FROM ssns) + "
    "(SELECT COUNT(*) FROM emails) + (SELECT COUNT(*) FROM phones) + (SELECT COUNT(*) FROM clients WHERE is_mule = 0) "
    "UNION ALL SELECT 2, (SELECT COUNT(*) FROM transactions) + (SELECT COUNT(*) FROM clients WHERE is_mule = 1)",
    # relationships per type
    "SELECT 'FIRST_TX', COUNT(*) FROM clients c JOIN transactions t ON t.id = c.first_tx_id UNION ALL "
    "SELECT 'HAS_EMAIL', COUNT(*) FROM clients c JOIN emails e ON e.email = c.email UNION ALL "
    "SELECT 'HAS_PHONE', COUNT(*) FROM clients c JOIN phones p ON p.phone_number = c.phone_number UNION ALL "
    "SELECT 'HAS_SSN', COUNT(*) FROM clients c JOIN ssns s ON s.ssn = c.ssn UNION ALL "
    "SELECT 'LAST_TX', COUNT(*) FROM clients c JOIN transactions t ON t.id = c.last_tx_id UNION ALL "
    "SELECT 'NEXT', COUNT(*) FROM transactions a JOIN transactions b ON b.id = a.next_transaction_id UNION ALL "
    "SELECT 'PERFORMED', COUNT(*) FROM transactions t JOIN clients c ON c.id = t.client_id UNION ALL "
    "SELECT 'TO', (SELECT COUNT(*) FROM transactions t JOIN merchants m ON m.id = t.to_merchant_id) + "
    "(SELECT COUNT(*) FROM transactions t JOIN clients c ON c.id = t.to_client_id) + "
    "(SELECT COUNT(*) FROM transactions t JOIN banks b ON b.id = t.to_bank_id)",
    # TO targets: merchant, client, mule, bank
    "SELECT (SELECT COUNT(*) FROM transactions t JOIN merchants m ON m.id = t.to_merchant_id), "
    "(SELECT COUNT(*) FROM transactions t JOIN clients c ON c.id = t.to_client_id), "
    "(SELECT COUNT(*) FROM transactions t JOIN clients c ON c.id = t.to_client_id WHERE c.is_mule = 1), "
    "(SELECT COUNT(*) FROM transactions t JOIN banks b ON b.id = t.to_bank_id)",
    per_type("CashIn"), per_type("CashOut"), per_type("Payment"), per_type("Transfer"), per_type("Debit"),
    f"SELECT COUNT(*), SUM(is_mule), SUM({cid('id')}), SUM(CHAR_LENGTH(name)) FROM clients",
    f"SELECT COUNT(*), SUM({dig('id')}), SUM(CHAR_LENGTH(name)) FROM "
    "(SELECT id, name FROM merchants UNION ALL SELECT id, name FROM banks) x",
    f"SELECT (SELECT COUNT(*) FROM ssns), (SELECT SUM({dig('ssn')}) FROM ssns), (SELECT COUNT(*) FROM emails), "
    f"(SELECT SUM(CHAR_LENGTH(email)) FROM emails), (SELECT COUNT(*) FROM phones), (SELECT SUM({dig('phone_number')}) FROM phones)",
    f"SELECT COUNT(*), SUM({tx('t.id')} * ({cid('c.id')} % 1000)) FROM transactions t JOIN clients c ON c.id = t.client_id",
    f"SELECT COUNT(*), SUM({tx('a.id')} * ({tx('b.id')} % 1000)) FROM transactions a JOIN transactions b ON b.id = a.next_transaction_id",
    f"SELECT 'FIRST_TX', COUNT(*), SUM({tx('t.id')} * ({cid('c.id')} % 1000)) FROM clients c JOIN transactions t ON t.id = c.first_tx_id "
    f"UNION ALL SELECT 'LAST_TX', COUNT(*), SUM({tx('t.id')} * ({cid('c.id')} % 1000)) FROM clients c JOIN transactions t ON t.id = c.last_tx_id",
    f"SELECT COUNT(*), SUM({tx('t.id')} * ({dig('m.id')} % 1000)) FROM transactions t JOIN merchants m ON m.id = t.to_merchant_id",
    f"SELECT COUNT(*), SUM({tx('t.id')} * ({cid('c.id')} % 1000)) FROM transactions t JOIN clients c ON c.id = t.to_client_id",
    f"SELECT COUNT(*), SUM({tx('t.id')} * ({dig('b.id')} % 1000)) FROM transactions t JOIN banks b ON b.id = t.to_bank_id",
    f"SELECT COUNT(*), SUM({cid('c.id')} * ({dig('s.ssn')} % 1000)) FROM clients c JOIN ssns s ON s.ssn = c.ssn",
    f"SELECT COUNT(*), SUM({cid('c.id')} * CHAR_LENGTH(e.email)) FROM clients c JOIN emails e ON e.email = c.email",
    f"SELECT COUNT(*), SUM({cid('c.id')} * ({dig('p.phone_number')} % 1000)) FROM clients c JOIN phones p ON p.phone_number = c.phone_number",
    "SELECT COUNT(*) FROM transactions t JOIN clients c ON c.id = t.to_client_id WHERE t.type = 'Transfer' AND t.fraud = 1 AND c.is_mule = 1",
    "SELECT COUNT(*) n FROM clients c JOIN ssns s ON s.ssn = c.ssn GROUP BY s.ssn ORDER BY n DESC LIMIT 5",
]
assert len(SQL) == len(CHECKS)

def mysql(q):
    p = subprocess.run(["/usr/local/mysql/bin/mysql", "-u", "root", "-h", "127.0.0.1", "-N", "-B", "fraud_1_1m", "-e", q],
                       capture_output=True, text=True, env={**os.environ, "MYSQL_PWD": "password"})
    if p.returncode:
        return ["ERROR: " + p.stderr.strip()]
    return [",".join(f.lower() for f in l.split("\t")) for l in p.stdout.strip().splitlines()]

fails = 0
for (name, cypher), sql in zip(CHECKS, SQL):
    t = time.time()
    a, b = neo4j(cypher), mysql(sql)
    ok = a == b and not any(x.startswith("error") for x in a + b)
    fails += not ok
    print(f"{'MATCH' if ok else 'DIFF '}  {name}  ({time.time() - t:.1f}s)")
    if not ok:
        print(f"         neo4j: {a}\n         mysql: {b}")
print(f"\n{len(CHECKS) - fails}/{len(CHECKS)} checks identical (Neo4j vs MySQL)")
sys.exit(1 if fails else 0)
