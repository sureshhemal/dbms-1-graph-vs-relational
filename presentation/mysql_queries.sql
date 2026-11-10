-- MySQL 8.4 (SQL): fraud detection query set, Team 6 Phase 2
-- Database: fraud_1_1m (127.0.0.1, user root)
-- Run the USE line once, then copy one query at a time and run it.

USE fraud_1_1m;

-- ======================================================================
-- PART 1: QUESTIONS ABOUT RECORDS
-- ======================================================================

-- R1. Fetch one transaction [Basic]
-- Show transaction tx-500000: its type, amount, fraud flag, hour, who sent it and who received it.
-- Benchmark median on this laptop: 0.3 ms
SELECT id, type, amount, fraud, step,
       client_id AS sender,
       COALESCE(to_merchant_id, to_client_id, to_bank_id) AS receiver
FROM transactions
WHERE id = 'tx-500000';

-- R2. Ten largest transfers [Basic]
-- List the ten biggest transfers ever made, with their amount and fraud flag.
-- Benchmark median on this laptop: 119.3 ms
SELECT id, amount, fraud
FROM transactions
WHERE type = 'Transfer'
ORDER BY amount DESC
LIMIT 10;

-- R3. Activity in a time window [Basic]
-- What happened between hour 1,000 and hour 1,010? Count and total the transactions of each type.
-- Benchmark median on this laptop: 1.8 s
SELECT type, COUNT(*) AS txs, ROUND(SUM(amount)) AS total
FROM transactions
WHERE step BETWEEN 1000 AND 1010
GROUP BY type
ORDER BY CAST(type AS CHAR);

-- R4. Fraud rate by transaction type [Intermediate]
-- Across all 1.07 million transactions: how many of each type, how many fraudulent, and the average amount.
-- Benchmark median on this laptop: 1.8 s
SELECT type, COUNT(*) AS txs, SUM(fraud) AS frauds,
       ROUND(AVG(amount), 2) AS avg_amount
FROM transactions
GROUP BY type
ORDER BY frauds DESC, CAST(type AS CHAR);

-- R5. Merchants hit hardest by fraud [Intermediate]
-- Which five merchants received the most money in fraudulent payments?
-- Benchmark median on this laptop: 10.7 ms
SELECT m.id AS merchant_id, m.name AS merchant, COUNT(*) AS frauds,
       ROUND(SUM(t.amount)) AS fraud_amount
FROM transactions t
JOIN merchants m ON m.id = t.to_merchant_id
WHERE t.type = 'Payment' AND t.fraud = 1
GROUP BY m.id, m.name
ORDER BY fraud_amount DESC
LIMIT 5;

-- R6. Hourly volume with running total [Advanced]
-- For the first 12 hours: transactions and money moved per hour, the running total so far, and the change from the previous hour.
-- Benchmark median on this laptop: 137.0 ms
SELECT step, COUNT(*) AS txs, ROUND(SUM(amount)) AS volume,
       ROUND(SUM(SUM(amount)) OVER (ORDER BY step)) AS running_total,
       COUNT(*) - LAG(COUNT(*)) OVER (ORDER BY step) AS `change`
FROM transactions
WHERE step <= 12
GROUP BY step
ORDER BY step;

-- ======================================================================
-- PART 2: QUESTIONS BETWEEN RECORDS
-- ======================================================================

-- G1. Shared-SSN identity rings [Intermediate]
-- Find SSNs used by 8 or more clients, a classic sign of synthetic identities, with how many of those clients are mules.
-- Benchmark median on this laptop: 16.5 ms
SELECT ssn, COUNT(*) AS clients, SUM(is_mule) AS mules,
       GROUP_CONCAT(name ORDER BY name SEPARATOR ', ') AS names
FROM clients
GROUP BY ssn
HAVING clients >= 8
ORDER BY clients DESC, ssn;

-- G2. Clients one identity away from a mule [Intermediate]
-- Which clients share an SSN, email or phone number with a known mule? These are the accounts to review next.
-- Benchmark median on this laptop: 25.2 ms
WITH links AS (
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
FROM links;

-- G3. Unflagged transfers into mule-linked accounts [Advanced]
-- Find transfers not marked as fraud whose receiver shares an SSN, email or phone with a known mule: likely fraud the bank has not caught yet.
-- Benchmark median on this laptop: 177.4 ms
WITH linked AS (
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
WHERE t.type = 'Transfer' AND t.fraud = 0;

-- G4. Money laundered through mules [Advanced]
-- Find chains where a client sends a fraudulent transfer to a mule, and the mule later passes money on to someone else (layering).
-- Benchmark median on this laptop: 12.2 s
SELECT COUNT(*) AS chains, COUNT(DISTINCT m.id) AS mules,
       COUNT(DISTINCT t2.to_client_id) AS receivers
FROM transactions t1
JOIN clients m       ON m.id = t1.to_client_id AND m.is_mule = 1
JOIN transactions t2 ON t2.client_id = m.id AND t2.type = 'Transfer'
                    AND t2.global_step > t1.global_step
WHERE t1.type = 'Transfer' AND t1.fraud = 1
  AND t2.to_client_id <> t1.client_id;

-- G5. Circular money flows [Advanced]
-- Find money that goes round in a circle: A transfers to B, B to C, and C back to A. Cycles like this are a classic sign of laundering.
-- Benchmark median on this laptop: 68.7 s
SELECT COUNT(*) AS cycles, COUNT(DISTINCT t1.client_id) AS clients_in_cycles
FROM transactions t1
JOIN transactions t2 ON t2.client_id = t1.to_client_id AND t2.type = 'Transfer'
JOIN transactions t3 ON t3.client_id = t2.to_client_id AND t3.type = 'Transfer'
                    AND t3.to_client_id = t1.client_id
WHERE t1.type = 'Transfer'
  AND t1.client_id <> t2.client_id
  AND t2.client_id <> t3.client_id
  AND t1.client_id <> t3.client_id;

-- G6. How far can a mule's money spread? [Expert]
-- Starting from mule Richard Davis, how many different clients can money reach in up to 3 transfers (mule -> A -> B -> C)?
-- Benchmark median on this laptop: 3.3 s
WITH RECURSIVE reach (id, depth) AS (
  SELECT CAST('4000000000000000' AS CHAR(32)), 0
  UNION DISTINCT
  SELECT t.to_client_id, r.depth + 1
  FROM reach r
  JOIN transactions t ON t.client_id = r.id
  WHERE t.type = 'Transfer' AND r.depth < 3
)
SELECT COUNT(DISTINCT id) AS reachable
FROM reach WHERE id <> '4000000000000000';

-- G7. Shortest identity link between two people [Expert]
-- How is client Donald Martinez connected to mule Richard Davis through shared SSNs, emails and phones, and through whom?
-- Benchmark median on this laptop: 973.4 ms
WITH RECURSIVE links (src, dst) AS (
  SELECT a.id, b.id FROM clients a JOIN clients b ON b.ssn = a.ssn AND b.id <> a.id
  UNION SELECT a.id, b.id FROM clients a JOIN clients b ON b.email = a.email AND b.id <> a.id
  UNION SELECT a.id, b.id FROM clients a JOIN clients b ON b.phone_number = a.phone_number AND b.id <> a.id
),
bfs (id, depth) AS (
  SELECT CAST('4000000000000000' AS CHAR(32)), 0
  UNION DISTINCT
  SELECT l.dst, b.depth + 1 FROM bfs b JOIN links l ON l.src = b.id
  WHERE b.depth < 20
)
SELECT MIN(depth) * 2 AS hops FROM bfs WHERE id = '4000000000001638';
