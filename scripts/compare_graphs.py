"""Run identical Cypher checks on Neo4j (fraudbench) and Memgraph and compare results."""
import subprocess, sys, time

# Fingerprints: numeric parts of ids multiplied across each edge, so a single
# misrouted relationship changes the sum. Products stay well under 2^63.
TXN = "toInteger(substring({v}.id, 3))"                      # "tx-123" -> 123
CID = "(toInteger({v}.id) % 1000003)"                        # 16-digit client id, reduced
DIG = "(toInteger(replace(replace({v}, '-', ''), 'B', '')) % 1000003)"  # "01-0000000", "B01-...", "000-00-0000"

def tx(v): return TXN.format(v=v)
def cid(v): return CID.format(v=v)
def dig(expr): return DIG.format(v=expr)

CHECKS = [
    ("total nodes", "MATCH (n) RETURN count(n)"),
    ("total relationships", "MATCH ()-[r]->() RETURN count(r)"),
    ("nodes per label", "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) ORDER BY l"),
    ("labels per node", "MATCH (n) RETURN size(labels(n)) AS k, count(*) ORDER BY k"),
    ("relationships per type", "MATCH ()-[r]->() RETURN type(r) AS t, count(*) ORDER BY t"),
    ("TO targets", "MATCH (:Transaction)-[:TO]->(x) RETURN "
        "sum(CASE WHEN x:Merchant THEN 1 ELSE 0 END), sum(CASE WHEN x:Client THEN 1 ELSE 0 END), "
        "sum(CASE WHEN x:Mule THEN 1 ELSE 0 END), sum(CASE WHEN x:Bank THEN 1 ELSE 0 END)"),
]
for lbl in ["CashIn", "CashOut", "Payment", "Transfer", "Debit"]:
    CHECKS.append((f"{lbl}: count, fraud, sum(amount cents), sum(step/globalStep/ts), id sum",
        f"MATCH (t:{lbl}) RETURN count(t), sum(CASE WHEN t.fraud THEN 1 ELSE 0 END), "
        f"sum(toInteger(round(t.amount * 100))), sum(t.step), sum(t.globalStep), sum(t.ts), sum({tx('t')})"))
CHECKS += [
    ("clients: count, mules, id fp, name chars",
        f"MATCH (c:Client) RETURN count(c), sum(CASE WHEN c:Mule THEN 1 ELSE 0 END), sum({cid('c')}), sum(size(c.name))"),
    ("merchants/banks: count, id fp, name chars",
        f"MATCH (m) WHERE m:Merchant OR m:Bank RETURN count(m), sum({dig('m.id')}), sum(size(m.name))"),
    ("SSN/Email/Phone: counts + value fp",
        f"MATCH (s:SSN) WITH count(s) AS ns, sum({dig('s.ssn')}) AS fs "
        f"MATCH (e:Email) WITH ns, fs, count(e) AS ne, sum(size(e.email)) AS fe "
        f"MATCH (p:Phone) RETURN ns, fs, ne, fe, count(p), sum({dig('p.phoneNumber')})"),
    ("PERFORMED edges fp", f"MATCH (c:Client)-[:PERFORMED]->(t) RETURN count(*), sum({tx('t')} * ({cid('c')} % 1000))"),
    ("NEXT edges fp", f"MATCH (a)-[:NEXT]->(b) RETURN count(*), sum({tx('a')} * ({tx('b')} % 1000))"),
    ("FIRST_TX / LAST_TX fp",
        f"MATCH (c:Client)-[r:FIRST_TX|LAST_TX]->(t) RETURN type(r) AS k, count(*), sum({tx('t')} * ({cid('c')} % 1000)) ORDER BY k"),
    ("TO->Merchant fp", f"MATCH (t)-[:TO]->(m:Merchant) RETURN count(*), sum({tx('t')} * ({dig('m.id')} % 1000))"),
    ("TO->Client fp", f"MATCH (t)-[:TO]->(c:Client) RETURN count(*), sum({tx('t')} * ({cid('c')} % 1000))"),
    ("TO->Bank fp", f"MATCH (t)-[:TO]->(b:Bank) RETURN count(*), sum({tx('t')} * ({dig('b.id')} % 1000))"),
    ("HAS_SSN fp", f"MATCH (c)-[:HAS_SSN]->(s) RETURN count(*), sum({cid('c')} * ({dig('s.ssn')} % 1000))"),
    ("HAS_EMAIL fp", f"MATCH (c)-[:HAS_EMAIL]->(e) RETURN count(*), sum({cid('c')} * size(e.email))"),
    ("HAS_PHONE fp", f"MATCH (c)-[:HAS_PHONE]->(p) RETURN count(*), sum({cid('c')} * ({dig('p.phoneNumber')} % 1000))"),
    ("fraud pattern: fraudulent Transfers to Mules", "MATCH (t:Transfer {fraud: true})-[:TO]->(m:Mule) RETURN count(*)"),
    ("largest shared-SSN rings (top 5)",
        "MATCH (s:SSN)<-[:HAS_SSN]-(c) WITH s, count(c) AS n RETURN n ORDER BY n DESC LIMIT 5"),
]

def norm(text):
    rows = [l.strip() for l in text.strip().splitlines() if l.strip()]
    return [",".join(f.strip().strip('"').lower() for f in r.split(",")) for r in rows[1:]]  # drop header

def neo4j(q):
    p = subprocess.run(["cypher-shell", "-u", "neo4j", "-p", "password", "-d", "fraudbench", "--format", "plain", q],
                       capture_output=True, text=True)
    return norm(p.stdout) if p.returncode == 0 else ["ERROR: " + p.stderr.strip()]

def memgraph(q):
    p = subprocess.run(["docker", "exec", "-i", "memgraph-mage", "mgconsole", "--host", "127.0.0.1", "--port", "7687",
                        "--output-format=csv"], input=q + ";\n", capture_output=True, text=True)
    return norm(p.stdout) if p.returncode == 0 else ["ERROR: " + (p.stderr or p.stdout).strip()]

def main():
    fails = 0
    for name, q in CHECKS:
        t = time.time()
        a, b = neo4j(q), memgraph(q)
        ok = a == b and not any(x.startswith("error") for x in a + b)
        fails += not ok
        print(f"{'MATCH' if ok else 'DIFF '}  {name}  ({time.time() - t:.1f}s)")
        if ok:
            for row in a: print(f"         {row}")
        else:
            print(f"         neo4j:    {a}\n         memgraph: {b}")
    print(f"\n{len(CHECKS) - fails}/{len(CHECKS)} checks identical")
    sys.exit(1 if fails else 0)

if __name__ == "__main__":
    main()
