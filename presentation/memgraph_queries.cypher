// Memgraph (Cypher dialect): fraud detection query set, Team 6 Phase 2
// Connection: bolt://localhost:7688 (Memgraph Lab or mgconsole)
// Differs from Neo4j only in R4 (one-argument round) and G7 (*BFS instead of shortestPath).
// Copy one query at a time (from the comment header down to the ';') and run it.

// ======================================================================
// PART 1: QUESTIONS ABOUT RECORDS
// ======================================================================

// R1. Fetch one transaction [Basic]
// Show transaction tx-500000: its type, amount, fraud flag, hour, who sent it and who received it.
// Benchmark median on this laptop: 0.6 ms
MATCH (c:Client)-[:PERFORMED]->(t:Transaction {id: "tx-500000"})-[:TO]->(x)
RETURN t.id AS id, [l IN labels(t) WHERE l <> "Transaction"][0] AS type,
       t.amount AS amount, t.fraud AS fraud, t.step AS step,
       c.id AS sender, x.id AS receiver;

// R2. Ten largest transfers [Basic]
// List the ten biggest transfers ever made, with their amount and fraud flag.
// Benchmark median on this laptop: 72.3 ms
MATCH (t:Transfer)
RETURN t.id AS id, t.amount AS amount, t.fraud AS fraud
ORDER BY amount DESC
LIMIT 10;

// R3. Activity in a time window [Basic]
// What happened between hour 1,000 and hour 1,010? Count and total the transactions of each type.
// Benchmark median on this laptop: 198.7 ms
MATCH (t:Transaction)
WHERE t.step >= 1000 AND t.step <= 1010
WITH [l IN labels(t) WHERE l <> "Transaction"][0] AS type, t
RETURN type, count(*) AS txs, round(sum(t.amount)) AS total
ORDER BY type;

// R4. Fraud rate by transaction type [Intermediate]
// Across all 1.07 million transactions: how many of each type, how many fraudulent, and the average amount.
// Benchmark median on this laptop: 575.4 ms
MATCH (t:Transaction)
WITH [l IN labels(t) WHERE l <> "Transaction"][0] AS type, t
RETURN type, count(*) AS txs,
       sum(CASE WHEN t.fraud THEN 1 ELSE 0 END) AS frauds,
       round(avg(t.amount) * 100) / 100 AS avg_amount
ORDER BY frauds DESC, type;

// R5. Merchants hit hardest by fraud [Intermediate]
// Which five merchants received the most money in fraudulent payments?
// Benchmark median on this laptop: 55.1 ms
MATCH (t:Payment {fraud: true})-[:TO]->(m:Merchant)
RETURN m.id AS merchant_id, m.name AS merchant, count(t) AS frauds,
       round(sum(t.amount)) AS fraud_amount
ORDER BY fraud_amount DESC
LIMIT 5;

// R6. Hourly volume with running total [Advanced]
// For the first 12 hours: transactions and money moved per hour, the running total so far, and the change from the previous hour.
// Benchmark median on this laptop: 108.6 ms
MATCH (t:Transaction)
WHERE t.step <= 12
WITH t.step AS step, count(*) AS txs, sum(t.amount) AS volume
ORDER BY step
WITH collect({step: step, txs: txs, volume: volume}) AS hours
UNWIND range(0, size(hours) - 1) AS i
WITH hours, i, hours[i] AS h
RETURN h.step AS step, h.txs AS txs, round(h.volume) AS volume,
       round(reduce(s = 0.0, x IN hours[0..i + 1] | s + x.volume)) AS running_total,
       CASE WHEN i = 0 THEN null ELSE h.txs - hours[i - 1].txs END AS change;

// ======================================================================
// PART 2: QUESTIONS BETWEEN RECORDS
// ======================================================================

// G1. Shared-SSN identity rings [Intermediate]
// Find SSNs used by 8 or more clients, a classic sign of synthetic identities, with how many of those clients are mules.
// Benchmark median on this laptop: 39.9 ms
MATCH (s:SSN)<-[:HAS_SSN]-(c:Client)
WITH s, c ORDER BY c.name
WITH s, count(c) AS clients,
     sum(CASE WHEN c:Mule THEN 1 ELSE 0 END) AS mules,
     collect(c.name) AS names
WHERE clients >= 8
RETURN s.ssn AS ssn, clients, mules, names
ORDER BY clients DESC, ssn;

// G2. Clients one identity away from a mule [Intermediate]
// Which clients share an SSN, email or phone number with a known mule? These are the accounts to review next.
// Benchmark median on this laptop: 36.1 ms
MATCH (m:Mule)-[:HAS_SSN|HAS_EMAIL|HAS_PHONE]->(id)
      <-[:HAS_SSN|HAS_EMAIL|HAS_PHONE]-(other:Client)
WHERE other.id <> m.id
RETURN count(DISTINCT other) AS linked_clients,
       count(DISTINCT m) AS mules, count(*) AS links;

// G3. Unflagged transfers into mule-linked accounts [Advanced]
// Find transfers not marked as fraud whose receiver shares an SSN, email or phone with a known mule: likely fraud the bank has not caught yet.
// Benchmark median on this laptop: 42.1 ms
MATCH (m:Mule)-[:HAS_SSN|HAS_EMAIL|HAS_PHONE]->()
      <-[:HAS_SSN|HAS_EMAIL|HAS_PHONE]-(r:Client)
WHERE NOT r:Mule
WITH DISTINCT r
MATCH (t:Transfer {fraud: false})-[:TO]->(r)
RETURN count(t) AS suspicious_transfers,
       count(DISTINCT r) AS accounts,
       round(sum(t.amount)) AS amount;

// G4. Money laundered through mules [Advanced]
// Find chains where a client sends a fraudulent transfer to a mule, and the mule later passes money on to someone else (layering).
// Benchmark median on this laptop: 1.0 s
MATCH (a:Client)-[:PERFORMED]->(t1:Transfer {fraud: true})-[:TO]->(m:Mule)
      -[:PERFORMED]->(t2:Transfer)-[:TO]->(b:Client)
WHERE t2.globalStep > t1.globalStep AND b <> a
RETURN count(*) AS chains, count(DISTINCT m) AS mules,
       count(DISTINCT b) AS receivers;

// G5. Circular money flows [Advanced]
// Find money that goes round in a circle: A transfers to B, B to C, and C back to A. Cycles like this are a classic sign of laundering.
// Benchmark median on this laptop: 34.0 s
MATCH (a:Client)-[:PERFORMED]->(:Transfer)-[:TO]->(b:Client)
      -[:PERFORMED]->(:Transfer)-[:TO]->(c:Client)
      -[:PERFORMED]->(:Transfer)-[:TO]->(a)
WHERE a <> b AND b <> c AND a <> c
RETURN count(*) AS cycles, count(DISTINCT a) AS clients_in_cycles;

// G6. How far can a mule's money spread? [Expert]
// Starting from mule Richard Davis, how many different clients can money reach in up to 3 transfers (mule -> A -> B -> C)?
// Benchmark median on this laptop: 8.4 s
MATCH (m:Client {id: "4000000000000000"})-[:PERFORMED|TO*2..6]->(c:Client)
WHERE c <> m
RETURN count(DISTINCT c) AS reachable;

// G7. Shortest identity link between two people [Expert]
// How is client Donald Martinez connected to mule Richard Davis through shared SSNs, emails and phones, and through whom?
// Benchmark median on this laptop: 29.3 ms
MATCH (a:Client {id: "4000000000000000"}), (b:Client {id: "4000000000001638"})
MATCH p = (a)-[:HAS_SSN|HAS_EMAIL|HAS_PHONE *BFS ..40]-(b)
RETURN size(relationships(p)) AS hops,
       [n IN nodes(p) WHERE n:Client | n.name] AS via_clients;
