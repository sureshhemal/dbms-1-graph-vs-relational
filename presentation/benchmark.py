"""Run the presentation queries on Neo4j, Memgraph and MySQL; check the answers agree; time them.

    .venv/bin/python presentation/benchmark.py              # 1 warm-up + 5 timed runs each
    .venv/bin/python presentation/benchmark.py --runs 10
    .venv/bin/python presentation/benchmark.py --only G5    # one query

Part 1 asks questions about records (relational territory), part 2 asks questions between
records (graph territory). Writes presentation/results.json. Timings are client-side wall clock
(execute + fetch every row) over one open connection per database, after a warm-up run.
Queries whose warm-up takes over 5 s get 3 timed runs instead of --runs (the assignment minimum).
Every individual run is stored, with its average and median. The script also records the
hardware, software versions and the configuration settings that matter for the comparison.

    .venv/bin/python presentation/benchmark.py --env-only   # refresh only the environment block
"""
import argparse, json, pathlib, platform, statistics, subprocess, sys, time
from decimal import Decimal

import neo4j, pymysql

TX = "tx-500000"
MULE = "4000000000000000"      # Richard Davis, a mule and the busiest account
FAR = "4000000000001638"       # Donald Martinez, 32 identity hops from MULE
SLOW_MS = 5000
SLOW_RUNS = 3          # the assignment asks for at least 3 runs per query and system

PARTS = {
    "records": {
        "title": "Questions about records",
        "blurb": "Fetch, filter, sort, total and rank rows. Each answer comes from the records themselves, with "
                 "relationships at most one join away. SQL states these questions most directly; on speed, "
                 "the engines stay within a few times of each other.",
    },
    "relationships": {
        "title": "Questions between records",
        "blurb": "Who is connected to whom, through what, and how far the money travels. The answer lives in the "
                 "links between records, which is where fraud hides and where graph databases were built to look.",
    },
}

QUERIES = [
    # ------------------------------------------------------------------ part 1: records
    {
        "id": "R1", "part": "records", "level": "Basic", "title": "Fetch one transaction",
        "problem": f"Show transaction {TX}: its type, amount, fraud flag, hour, who sent it and who received it.",
        "compare_cols": 7,
        "neo4j": f"""MATCH (c:Client)-[:PERFORMED]->(t:Transaction {{id: "{TX}"}})-[:TO]->(x)
RETURN t.id AS id, [l IN labels(t) WHERE l <> "Transaction"][0] AS type,
       t.amount AS amount, t.fraud AS fraud, t.step AS step,
       c.id AS sender, x.id AS receiver""",
        "memgraph": "same",
        "mysql": f"""SELECT id, type, amount, fraud, step,
       client_id AS sender,
       COALESCE(to_merchant_id, to_client_id, to_bank_id) AS receiver
FROM transactions
WHERE id = '{TX}'""",
        "differences": [
            "In SQL the whole answer is one row: sender and receiver are just columns. The graph has to walk two relationships to find them, because who-sent-what is stored as edges.",
            "The type is a column in SQL but a second label in the graph, so Cypher has to pick it out of labels(t).",
            "All three are index lookups and finish in about a millisecond: for a single record, the model makes no practical difference.",
        ],
    },
    {
        "id": "R2", "part": "records", "level": "Basic", "title": "Ten largest transfers",
        "problem": "List the ten biggest transfers ever made, with their amount and fraud flag.",
        "compare_cols": 3,
        "neo4j": """MATCH (t:Transfer)
RETURN t.id AS id, t.amount AS amount, t.fraud AS fraud
ORDER BY amount DESC
LIMIT 10""",
        "memgraph": "same",
        "mysql": """SELECT id, amount, fraud
FROM transactions
WHERE type = 'Transfer'
ORDER BY amount DESC
LIMIT 10""",
        "differences": [
            "Line for line the same query: filter, sort, limit. A label in Cypher plays the part of the WHERE type = ... filter.",
            "No relationships are involved, so the graph brings nothing extra here.",
        ],
    },
    {
        "id": "R3", "part": "records", "level": "Basic", "title": "Activity in a time window",
        "problem": "What happened between hour 1,000 and hour 1,010? Count and total the transactions of each type.",
        "compare_cols": 3,
        "neo4j": """MATCH (t:Transaction)
WHERE t.step >= 1000 AND t.step <= 1010
WITH [l IN labels(t) WHERE l <> "Transaction"][0] AS type, t
RETURN type, count(*) AS txs, round(sum(t.amount)) AS total
ORDER BY type""",
        "memgraph": "same",
        "mysql": """SELECT type, COUNT(*) AS txs, ROUND(SUM(amount)) AS total
FROM transactions
WHERE step BETWEEN 1000 AND 1010
GROUP BY type
ORDER BY CAST(type AS CHAR)""",
        "differences": [
            "SQL has BETWEEN and GROUP BY; Cypher spells the range out and groups implicitly by whatever is not aggregated.",
            "step has no index in any of the three engines, so each one scans every transaction: a pure test of scanning records.",
            "Gotcha: type is an ENUM in MySQL, and ORDER BY sorts an ENUM by its declared order (CashIn, CashOut, Payment, Transfer, Debit), not alphabetically. CAST(type AS CHAR) makes it sort like Cypher's strings.",
        ],
    },
    {
        "id": "R4", "part": "records", "level": "Intermediate", "title": "Fraud rate by transaction type",
        "problem": "Across all 1.07 million transactions: how many of each type, how many fraudulent, and the average amount.",
        "compare_cols": 4,
        "neo4j": """MATCH (t:Transaction)
WITH [l IN labels(t) WHERE l <> "Transaction"][0] AS type, t
RETURN type, count(*) AS txs,
       sum(CASE WHEN t.fraud THEN 1 ELSE 0 END) AS frauds,
       round(avg(t.amount), 2) AS avg_amount
ORDER BY frauds DESC, type""",
        "memgraph": """MATCH (t:Transaction)
WITH [l IN labels(t) WHERE l <> "Transaction"][0] AS type, t
RETURN type, count(*) AS txs,
       sum(CASE WHEN t.fraud THEN 1 ELSE 0 END) AS frauds,
       round(avg(t.amount) * 100) / 100 AS avg_amount
ORDER BY frauds DESC, type""",
        "mysql": """SELECT type, COUNT(*) AS txs, SUM(fraud) AS frauds,
       ROUND(AVG(amount), 2) AS avg_amount
FROM transactions
GROUP BY type
ORDER BY frauds DESC, CAST(type AS CHAR)""",
        "differences": [
            "The classic report query. SQL states it directly; Cypher first has to turn a label back into a value it can group by.",
            "fraud is a 0/1 column in SQL, so SUM(fraud) counts frauds. Cypher stores a boolean and needs CASE WHEN.",
            "Dialect gap: Memgraph's round() takes one argument, so round(x, 2) becomes round(x * 100) / 100.",
        ],
    },
    {
        "id": "R5", "part": "records", "level": "Intermediate", "title": "Merchants hit hardest by fraud",
        "problem": "Which five merchants received the most money in fraudulent payments?",
        "compare_cols": 4,
        "neo4j": """MATCH (t:Payment {fraud: true})-[:TO]->(m:Merchant)
RETURN m.id AS merchant_id, m.name AS merchant, count(t) AS frauds,
       round(sum(t.amount)) AS fraud_amount
ORDER BY fraud_amount DESC
LIMIT 5""",
        "memgraph": "same",
        "mysql": """SELECT m.id AS merchant_id, m.name AS merchant, COUNT(*) AS frauds,
       ROUND(SUM(t.amount)) AS fraud_amount
FROM transactions t
JOIN merchants m ON m.id = t.to_merchant_id
WHERE t.type = 'Payment' AND t.fraud = 1
GROUP BY m.id, m.name
ORDER BY fraud_amount DESC
LIMIT 5""",
        "differences": [
            "One relationship against one JOIN: the same shape. A single join on an indexed key is SQL's home ground.",
            "The graph never says how the two connect (-[:TO]-> is stored data); the SQL has to state ON m.id = t.to_merchant_id.",
            "Gotcha: merchant names are not unique. RETURN m.name alone would make Cypher merge different merchants that share a name, so the query returns m.id as well, just as the SQL groups by m.id.",
        ],
    },
    {
        "id": "R6", "part": "records", "level": "Advanced", "title": "Hourly volume with running total",
        "problem": "For the first 12 hours: transactions and money moved per hour, the running total so far, and the change from the previous hour.",
        "compare_cols": 5,
        "neo4j": """MATCH (t:Transaction)
WHERE t.step <= 12
WITH t.step AS step, count(*) AS txs, sum(t.amount) AS volume
ORDER BY step
WITH collect({step: step, txs: txs, volume: volume}) AS hours
UNWIND range(0, size(hours) - 1) AS i
WITH hours, i, hours[i] AS h
RETURN h.step AS step, h.txs AS txs, round(h.volume) AS volume,
       round(reduce(s = 0.0, x IN hours[0..i + 1] | s + x.volume)) AS running_total,
       CASE WHEN i = 0 THEN null ELSE h.txs - hours[i - 1].txs END AS change""",
        "memgraph": "same",
        "mysql": """SELECT step, COUNT(*) AS txs, ROUND(SUM(amount)) AS volume,
       ROUND(SUM(SUM(amount)) OVER (ORDER BY step)) AS running_total,
       COUNT(*) - LAG(COUNT(*)) OVER (ORDER BY step) AS `change`
FROM transactions
WHERE step <= 12
GROUP BY step
ORDER BY step""",
        "differences": [
            "SQL window functions (SUM ... OVER, LAG) give running totals and previous-row comparisons in one line each.",
            "Cypher has no window functions. The workaround collects the hours into a list, unwinds it by position and folds the running total with reduce(): it works, but it is a puzzle to read.",
            "The clearest case of the relational model fitting the question better: this is analytics over ordered rows, with no relationships in sight.",
        ],
    },
    # ------------------------------------------------------------------ part 2: relationships
    {
        "id": "G1", "part": "relationships", "level": "Intermediate", "title": "Shared-SSN identity rings",
        "problem": "Find SSNs used by 8 or more clients, a classic sign of synthetic identities, with how many of those clients are mules.",
        "compare_cols": 3,
        "neo4j": """MATCH (s:SSN)<-[:HAS_SSN]-(c:Client)
WITH s, c ORDER BY c.name
WITH s, count(c) AS clients,
     sum(CASE WHEN c:Mule THEN 1 ELSE 0 END) AS mules,
     collect(c.name) AS names
WHERE clients >= 8
RETURN s.ssn AS ssn, clients, mules, names
ORDER BY clients DESC, ssn""",
        "memgraph": "same",
        "mysql": """SELECT ssn, COUNT(*) AS clients, SUM(is_mule) AS mules,
       GROUP_CONCAT(name ORDER BY name SEPARATOR ', ') AS names
FROM clients
GROUP BY ssn
HAVING clients >= 8
ORDER BY clients DESC, ssn""",
        "differences": [
            "The crossover question. One shared value is still a records question in SQL: the SSN is a column, so this is one GROUP BY ... HAVING and MySQL keeps pace.",
            "Cypher filters aggregates with WITH ... WHERE (there is no HAVING) and returns a real list with collect(); SQL flattens it into a string.",
            "It gets harder for SQL as soon as the question takes a second step, as the next queries show.",
        ],
    },
    {
        "id": "G2", "part": "relationships", "level": "Intermediate", "title": "Clients one identity away from a mule",
        "problem": "Which clients share an SSN, email or phone number with a known mule? These are the accounts to review next.",
        "compare_cols": 3,
        "neo4j": """MATCH (m:Mule)-[:HAS_SSN|HAS_EMAIL|HAS_PHONE]->(id)
      <-[:HAS_SSN|HAS_EMAIL|HAS_PHONE]-(other:Client)
WHERE other.id <> m.id
RETURN count(DISTINCT other) AS linked_clients,
       count(DISTINCT m) AS mules, count(*) AS links""",
        "memgraph": "same",
        "mysql": """WITH links AS (
  SELECT m.id AS mule_id, o.id AS other_id FROM clients m
    JOIN clients o ON o.ssn = m.ssn AND o.id <> m.id WHERE m.is_mule = 1
  UNION ALL
  SELECT m.id, o.id FROM clients m
    JOIN clients o ON o.email = m.email AND o.id <> m.id WHERE m.is_mule = 1
  UNION ALL
  SELECT m.id, o.id FROM clients m
    JOIN clients o ON o.phone_number = m.phone_number AND o.id <> m.id WHERE m.is_mule = 1
)
SELECT COUNT(DISTINCT other_id) AS linked_clients,
       COUNT(DISTINCT mule_id) AS mules, COUNT(*) AS links
FROM links""",
        "differences": [
            "One pattern with three alternative relationship types against three self-joins stitched together with UNION ALL.",
            "Adding a fourth identifier (a device ID, say) is one more type in the pattern, and one more UNION branch in SQL.",
        ],
    },
    {
        "id": "G3", "part": "relationships", "level": "Advanced", "title": "Unflagged transfers into mule-linked accounts",
        "problem": "Find transfers not marked as fraud whose receiver shares an SSN, email or phone with a known mule: likely fraud the bank has not caught yet.",
        "compare_cols": 3,
        "neo4j": """MATCH (m:Mule)-[:HAS_SSN|HAS_EMAIL|HAS_PHONE]->()
      <-[:HAS_SSN|HAS_EMAIL|HAS_PHONE]-(r:Client)
WHERE NOT r:Mule
WITH DISTINCT r
MATCH (t:Transfer {fraud: false})-[:TO]->(r)
RETURN count(t) AS suspicious_transfers,
       count(DISTINCT r) AS accounts,
       round(sum(t.amount)) AS amount""",
        "memgraph": "same",
        "mysql": """WITH linked AS (
  SELECT o.id FROM clients m
    JOIN clients o ON o.ssn = m.ssn WHERE m.is_mule = 1 AND o.is_mule = 0
  UNION
  SELECT o.id FROM clients m
    JOIN clients o ON o.email = m.email WHERE m.is_mule = 1 AND o.is_mule = 0
  UNION
  SELECT o.id FROM clients m
    JOIN clients o ON o.phone_number = m.phone_number WHERE m.is_mule = 1 AND o.is_mule = 0
)
SELECT COUNT(*) AS suspicious_transfers,
       COUNT(DISTINCT t.to_client_id) AS accounts,
       ROUND(SUM(t.amount)) AS amount
FROM transactions t
JOIN linked l ON l.id = t.to_client_id
WHERE t.type = 'Transfer' AND t.fraud = 0""",
        "differences": [
            "This is fraud detection rather than reporting: it finds transactions nobody has flagged by looking at who the receiver is connected to.",
            "Cypher chains two patterns: find the mule-linked accounts, then follow the money into them. SQL builds the same set from three self-joins before it can join the transactions.",
        ],
    },
    {
        "id": "G4", "part": "relationships", "level": "Advanced", "title": "Money laundered through mules",
        "problem": "Find chains where a client sends a fraudulent transfer to a mule, and the mule later passes money on to someone else (layering).",
        "compare_cols": 3,
        "neo4j": """MATCH (a:Client)-[:PERFORMED]->(t1:Transfer {fraud: true})-[:TO]->(m:Mule)
      -[:PERFORMED]->(t2:Transfer)-[:TO]->(b:Client)
WHERE t2.globalStep > t1.globalStep AND b <> a
RETURN count(*) AS chains, count(DISTINCT m) AS mules,
       count(DISTINCT b) AS receivers""",
        "memgraph": "same",
        "mysql": """SELECT COUNT(*) AS chains, COUNT(DISTINCT m.id) AS mules,
       COUNT(DISTINCT t2.to_client_id) AS receivers
FROM transactions t1
JOIN clients m       ON m.id = t1.to_client_id AND m.is_mule = 1
JOIN transactions t2 ON t2.client_id = m.id AND t2.type = 'Transfer'
                    AND t2.global_step > t1.global_step
WHERE t1.type = 'Transfer' AND t1.fraud = 1
  AND t2.to_client_id <> t1.client_id""",
        "differences": [
            "The Cypher pattern reads like the story: client -> transfer -> mule -> transfer -> client. The SQL hides the same chain in three join conditions.",
            "Both put the time ordering (t2 after t1) in a filter; neither model gets that for free.",
        ],
    },
    {
        "id": "G5", "part": "relationships", "level": "Advanced", "title": "Circular money flows",
        "problem": "Find money that goes round in a circle: A transfers to B, B to C, and C back to A. Cycles like this are a classic sign of laundering.",
        "compare_cols": 2,
        "neo4j": """MATCH (a:Client)-[:PERFORMED]->(:Transfer)-[:TO]->(b:Client)
      -[:PERFORMED]->(:Transfer)-[:TO]->(c:Client)
      -[:PERFORMED]->(:Transfer)-[:TO]->(a)
WHERE a <> b AND b <> c AND a <> c
RETURN count(*) AS cycles, count(DISTINCT a) AS clients_in_cycles""",
        "memgraph": "same",
        "mysql": """SELECT COUNT(*) AS cycles, COUNT(DISTINCT t1.client_id) AS clients_in_cycles
FROM transactions t1
JOIN transactions t2 ON t2.client_id = t1.to_client_id AND t2.type = 'Transfer'
JOIN transactions t3 ON t3.client_id = t2.to_client_id AND t3.type = 'Transfer'
                    AND t3.to_client_id = t1.client_id
WHERE t1.type = 'Transfer'
  AND t1.client_id <> t2.client_id
  AND t2.client_id <> t3.client_id
  AND t1.client_id <> t3.client_id""",
        "differences": [
            "The pattern closes on itself: the last node is the same variable a as the first. That is the whole cycle check.",
            "SQL needs a three-way self-join of a million-row table and states the closing condition by hand (t3.to_client_id = t1.client_id).",
            "A 4-step cycle is one more hop in the pattern; in SQL it is a fourth self-join of the whole table, and the cost multiplies again.",
        ],
    },
    {
        "id": "G6", "part": "relationships", "level": "Expert", "title": "How far can a mule's money spread?",
        "problem": "Starting from mule Richard Davis, how many different clients can money reach in up to 3 transfers (mule -> A -> B -> C)?",
        "compare_cols": 1,
        "neo4j": f"""MATCH (m:Client {{id: "{MULE}"}})-[:PERFORMED|TO*2..6]->(c:Client)
WHERE c <> m
RETURN count(DISTINCT c) AS reachable""",
        "memgraph": "same",
        "mysql": f"""WITH RECURSIVE reach (id, depth) AS (
  SELECT CAST('{MULE}' AS CHAR(32)), 0
  UNION DISTINCT
  SELECT t.to_client_id, r.depth + 1
  FROM reach r
  JOIN transactions t ON t.client_id = r.id
  WHERE t.type = 'Transfer' AND r.depth < 3
)
SELECT COUNT(DISTINCT id) AS reachable
FROM reach WHERE id <> '{MULE}'""",
        "differences": [
            "Cypher puts 'any number of hops' into the pattern itself: *2..6 relationships means 1 to 3 transfers. SQL needs a recursive CTE with its own depth counter.",
            "The SQL rebuilds the graph's shape by hand (client_id -> to_client_id, type = 'Transfer'). The graph already knows only transfers lead to clients.",
            "Richard Davis is the busiest account in the data (53,692 transactions), which makes this the heaviest traversal in the set.",
        ],
    },
    {
        "id": "G7", "part": "relationships", "level": "Expert", "title": "Shortest identity link between two people",
        "problem": "How is client Donald Martinez connected to mule Richard Davis through shared SSNs, emails and phones, and through whom?",
        "compare_cols": 1,
        "neo4j": f"""MATCH (a:Client {{id: "{MULE}"}}), (b:Client {{id: "{FAR}"}})
MATCH p = shortestPath((a)-[:HAS_SSN|HAS_EMAIL|HAS_PHONE*..40]-(b))
RETURN length(p) AS hops,
       [n IN nodes(p) WHERE n:Client | n.name] AS via_clients""",
        "memgraph": f"""MATCH (a:Client {{id: "{MULE}"}}), (b:Client {{id: "{FAR}"}})
MATCH p = (a)-[:HAS_SSN|HAS_EMAIL|HAS_PHONE *BFS ..40]-(b)
RETURN size(relationships(p)) AS hops,
       [n IN nodes(p) WHERE n:Client | n.name] AS via_clients""",
        "mysql": f"""WITH RECURSIVE links (src, dst) AS (
  SELECT a.id, b.id FROM clients a JOIN clients b ON b.ssn = a.ssn AND b.id <> a.id
  UNION SELECT a.id, b.id FROM clients a JOIN clients b ON b.email = a.email AND b.id <> a.id
  UNION SELECT a.id, b.id FROM clients a JOIN clients b ON b.phone_number = a.phone_number AND b.id <> a.id
),
bfs (id, depth) AS (
  SELECT CAST('{MULE}' AS CHAR(32)), 0
  UNION DISTINCT
  SELECT l.dst, b.depth + 1 FROM bfs b JOIN links l ON l.src = b.id
  WHERE b.depth < 20
)
SELECT MIN(depth) * 2 AS hops FROM bfs WHERE id = '{FAR}'""",
        "differences": [
            "Neo4j has shortestPath(); Memgraph rejects it and uses its own *BFS syntax inside the pattern. This is the biggest dialect gap between the two graph engines.",
            "Both graphs return the actual chain of 17 people. The SQL returns only the distance: listing the people would mean carrying path strings through the recursion, which multiplies the work.",
            "The SQL rebuilds person-to-person links from three self-joins and needs a depth limit (20) chosen in advance.",
        ],
    },
]


def normalise(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, float):
        v = round(v, 2)
        return int(v) if v.is_integer() else v
    return v


class Neo4jLike:
    def __init__(self, uri, auth, database=None):
        self.driver = neo4j.GraphDatabase.driver(uri, auth=auth)
        self.session = self.driver.session(**({"database": database} if database else {}))

    def run(self, q):
        res = self.session.run(q)
        return list(res.keys()), [list(r.values()) for r in res]

    def close(self):
        self.session.close()
        self.driver.close()


class MySQL:
    def __init__(self):
        self.con = pymysql.connect(host="127.0.0.1", user="root", password="password", database="fraud_1_1m")

    def run(self, q):
        with self.con.cursor() as cur:
            cur.execute(q)
            return [d[0] for d in cur.description], [list(r) for r in cur.fetchall()]

    def close(self):
        self.con.close()


def sh(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception as e:  # tool missing on this machine
        return f"unavailable ({type(e).__name__})"


def safe(fn):
    try:
        return fn()
    except Exception as e:
        return f"unavailable ({type(e).__name__}: {str(e)[:120]})"


def collect_env(engines):
    """Hardware, software versions and the settings that shape the timings."""
    env = {"hardware": {}, "software": {}, "config": {}}
    hw = env["hardware"]
    if platform.system() == "Darwin":
        hw["model"] = sh("sysctl", "-n", "hw.model")
        hw["cpu"] = sh("sysctl", "-n", "machdep.cpu.brand_string")
        hw["cores"] = f'{sh("sysctl", "-n", "hw.perflevel0.physicalcpu")} performance + ' \
                      f'{sh("sysctl", "-n", "hw.perflevel1.physicalcpu")} efficiency ({sh("sysctl", "-n", "hw.ncpu")} total)'
        mem = sh("sysctl", "-n", "hw.memsize")
        hw["memory"] = f"{int(mem) / 2**30:.0f} GiB" if mem.isdigit() else mem
        hw["os"] = f'macOS {sh("sw_vers", "-productVersion")} ({sh("sw_vers", "-buildVersion")})'
    else:
        hw["cpu"] = platform.processor() or platform.machine()
        hw["os"] = platform.platform()
    hw["arch"] = platform.machine()

    sw = env["software"]
    neo, mg, my = engines["neo4j"], engines["memgraph"], engines["mysql"]
    sw["neo4j"] = safe(lambda: " ".join(str(v) for v in neo.run(
        "CALL dbms.components() YIELD name, versions, edition RETURN name, versions[0], edition")[1][0]))
    sw["memgraph"] = safe(lambda: f'Memgraph {mg.run("SHOW VERSION")[1][0][0]}')
    sw["memgraph_image"] = sh("docker", "inspect", "memgraph-mage", "--format", "{{.Config.Image}}")
    sw["mysql"] = safe(lambda: "MySQL " + " ".join(str(v) for v in my.run(
        "SELECT VERSION(), @@version_comment")[1][0]))
    sw["colima"] = sh("colima", "version").splitlines()[0] if "unavailable" not in sh("colima", "version") else sh("colima", "version")
    sw["docker"] = sh("docker", "version", "--format", "{{.Server.Version}}")
    sw["python"] = sys.version.split()[0]
    sw["neo4j_driver"] = neo4j.__version__
    sw["pymysql"] = pymysql.__version__

    cfg = env["config"]
    cfg["neo4j"] = safe(lambda: {r[0]: r[1] for r in neo.run(
        "SHOW SETTINGS YIELD name, value WHERE name IN ['server.memory.heap.initial_size', "
        "'server.memory.heap.max_size', 'server.memory.pagecache.size', 'db.query.default_language', "
        "'server.cypher.parallel.worker_limit'] RETURN name, value")[1]})
    cfg["memgraph"] = safe(lambda: {r[0]: r[1] for r in mg.run("SHOW STORAGE INFO")[1]
                                    if r[0] in ("storage_mode", "isolation_level", "memory_res", "vertex_count",
                                                "edge_count", "average_degree")})
    cfg["mysql"] = safe(lambda: {r[0]: r[1] for r in my.run(
        "SHOW VARIABLES WHERE Variable_name IN ('innodb_buffer_pool_size', 'innodb_buffer_pool_instances', "
        "'join_buffer_size', 'sort_buffer_size', 'tmp_table_size', 'max_heap_table_size', "
        "'cte_max_recursion_depth', 'innodb_flush_log_at_trx_commit')")[1]})
    colima = sh("colima", "list", "--json")
    cfg["colima_vm"] = safe(lambda: {k: v for k, v in json.loads(colima.splitlines()[0]).items()
                                     if k in ("name", "status", "arch", "cpus", "memory", "disk", "runtime")})
    return env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--only", nargs="*", help="query ids to run, e.g. R1 G5")
    ap.add_argument("--env-only", action="store_true", help="only refresh hardware / version / config info")
    args = ap.parse_args()

    engines = {
        "neo4j": Neo4jLike("bolt://localhost:7687", ("neo4j", "password"), "fraudbench"),
        "memgraph": Neo4jLike("bolt://localhost:7688", None),
        "mysql": MySQL(),
    }
    path = pathlib.Path(__file__).with_name("results.json")
    if args.env_only:
        data = json.loads(path.read_text())
        data["env"] = collect_env(engines)
        path.write_text(json.dumps(data, indent=2, default=str))
        print(json.dumps(data["env"], indent=2, default=str))
        for eng in engines.values():
            eng.close()
        return
    previous = {}
    if args.only and path.exists():
        previous = {q["id"]: q for q in json.loads(path.read_text())["queries"]}
    out = {"generated": time.strftime("%Y-%m-%d %H:%M"), "runs": max(args.runs, 3), "slow_runs": SLOW_RUNS, "slow_ms": SLOW_MS,
           "machine": f"{platform.system()} {platform.machine()}", "parts": PARTS, "queries": []}

    for q in QUERIES:
        if args.only and q["id"] not in args.only:
            if q["id"] in previous:
                out["queries"].append(previous[q["id"]])
            continue
        entry = dict(q)
        if entry["memgraph"] == "same":
            entry["memgraph"] = entry["neo4j"]
            entry["memgraph_same"] = True
        entry["results"], entry["timings_ms"] = {}, {}
        answers = {}
        for name, eng in engines.items():
            text = entry[name]
            t = time.perf_counter()
            cols, rows = eng.run(text)                      # warm-up, also the answer we check
            warm = (time.perf_counter() - t) * 1000
            runs = SLOW_RUNS if warm > SLOW_MS else max(args.runs, 3)
            times = []
            for _ in range(runs):
                t = time.perf_counter()
                eng.run(text)
                times.append((time.perf_counter() - t) * 1000)
            entry["results"][name] = {"columns": cols, "rows": [[normalise(v) for v in r] for r in rows]}
            entry["timings_ms"][name] = {"median": round(statistics.median(times), 1), "runs": runs,
                                         "mean": round(statistics.fmean(times), 1),
                                         "min": round(min(times), 1), "max": round(max(times), 1),
                                         "times": [round(x, 2) for x in times], "warmup": round(warm, 2)}
            answers[name] = [[normalise(v) for v in r[: q["compare_cols"]]] for r in rows]
        entry["answers_agree"] = answers["neo4j"] == answers["memgraph"] == answers["mysql"]
        out["queries"].append(entry)
        t = entry["timings_ms"]
        print(f"{q['id']} {q['title']:<46} agree={entry['answers_agree']!s:<5} "
              f"neo4j {t['neo4j']['median']:>9.1f} ms  memgraph {t['memgraph']['median']:>9.1f} ms  "
              f"mysql {t['mysql']['median']:>9.1f} ms", flush=True)
        if not entry["answers_agree"]:
            for name, a in answers.items():
                print(f"    {name}: {a[:3]}")

    out["env"] = collect_env(engines)
    for eng in engines.values():
        eng.close()
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
