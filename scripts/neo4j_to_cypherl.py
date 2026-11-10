#!/usr/bin/env python3
"""Export a Neo4j database as a Memgraph-compatible CYPHERL file.

Reads the graph over Neo4j's HTTP Query API (no driver or APOC needed) and
writes one Cypher statement per line, which Memgraph loads with:

    docker exec -i memgraph-mage mgconsole < stackoverflow50.cypherl

Nodes are matched by their natural key: for each label a single-property
uniqueness constraint is reused as the key, so relationships are created with
indexed lookups. A label without such a constraint falls back to a temporary
`_nid` property that is indexed during the load and removed at the end.
"""

import argparse
import base64
import json
import re
import sys
import urllib.error
import urllib.request

PAGE = 2000
SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TEMP_KEY = "_nid"


class Neo4j:
    def __init__(self, base_url, database, user, password):
        self.url = f"{base_url.rstrip('/')}/db/{database}/query/v2"
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def run(self, statement, **parameters):
        body = json.dumps({"statement": statement, "parameters": parameters}).encode()
        req = urllib.request.Request(self.url, data=body, headers=self.headers)
        try:
            with urllib.request.urlopen(req) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            sys.exit(f"Query failed ({exc.code}): {exc.read().decode(errors='replace')}")
        if payload.get("errors"):
            sys.exit(f"Query failed: {payload['errors']}")
        data = payload["data"]
        return [dict(zip(data["fields"], row)) for row in data["values"]]

    def paginate(self, statement):
        """Run a statement carrying a trailing SKIP $skip LIMIT $limit."""
        skip = 0
        while True:
            rows = self.run(statement, skip=skip, limit=PAGE)
            if not rows:
                return
            yield from rows
            if len(rows) < PAGE:
                return
            skip += PAGE


def ident(name):
    return name if SAFE_IDENT.match(name) else "`" + name.replace("`", "``") + "`"


def literal(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return '"' + escape(value) + '"'
    if isinstance(value, list):
        return "[" + ", ".join(literal(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{ident(k)}: {literal(v)}" for k, v in value.items()) + "}"
    sys.exit(f"Unsupported property type {type(value).__name__}: {value!r}")


def escape(text):
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def props_map(properties):
    return ", ".join(f"{ident(k)}: {literal(v)}" for k, v in sorted(properties.items()))


def unique_keys(db):
    """Map each label to a single-property uniqueness key, where one exists."""
    keys = {}
    for row in db.run("SHOW CONSTRAINTS YIELD type, labelsOrTypes, properties"):
        if row["type"] != "NODE_PROPERTY_UNIQUENESS":
            continue
        labels, properties = row["labelsOrTypes"], row["properties"]
        if len(labels) == 1 and len(properties) == 1:
            keys.setdefault(labels[0], properties[0])
    return keys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:7474")
    parser.add_argument("--database", default="stackoverflow50")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="password")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    db = Neo4j(args.url, args.database, args.user, args.password)

    labels = [r["label"] for r in db.run("CALL db.labels() YIELD label RETURN label")]
    rel_types = [
        r["relationshipType"]
        for r in db.run(
            "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"
        )
    ]
    keys = unique_keys(db)
    fallback = sorted(set(labels) - set(keys))

    # elementId -> (label, key property, key value), used to wire up relationships.
    index = {}
    nodes = []
    for label in labels:
        key = keys.get(label, TEMP_KEY)
        statement = (
            f"MATCH (n:{ident(label)}) RETURN elementId(n) AS eid, "
            "properties(n) AS props ORDER BY eid SKIP $skip LIMIT $limit"
        )
        for row in db.paginate(statement):
            properties = row["props"]
            if label in keys:
                if key not in properties:
                    sys.exit(f"Node {row['eid']} (:{label}) is missing key '{key}'")
                value = properties[key]
            else:
                value = len(index)
                properties = dict(properties, **{TEMP_KEY: value})
            index[row["eid"]] = (label, key, value)
            nodes.append((label, properties))

    relationships = []
    for rel_type in rel_types:
        statement = (
            f"MATCH (a)-[r:{ident(rel_type)}]->(b) RETURN elementId(r) AS rid, "
            "elementId(a) AS src, elementId(b) AS dst, properties(r) AS props "
            "ORDER BY rid SKIP $skip LIMIT $limit"
        )
        for row in db.paginate(statement):
            relationships.append((rel_type, row["src"], row["dst"], row["props"]))

    with open(args.out, "w", encoding="utf-8") as out:
        emit = lambda line: out.write(line + "\n")

        # Indexes first: every relationship below is two key lookups.
        for label, key in sorted(keys.items()):
            emit(f"CREATE INDEX ON :{ident(label)}({ident(key)});")
        for label in fallback:
            emit(f"CREATE INDEX ON :{ident(label)}({ident(TEMP_KEY)});")

        for label, properties in nodes:
            emit(f"CREATE (:{ident(label)} {{{props_map(properties)}}});")

        for rel_type, src, dst, properties in relationships:
            a_label, a_key, a_value = index[src]
            b_label, b_key, b_value = index[dst]
            body = f"[:{ident(rel_type)}" + (
                f" {{{props_map(properties)}}}]" if properties else "]"
            )
            emit(
                f"MATCH (a:{ident(a_label)} {{{ident(a_key)}: {literal(a_value)}}}), "
                f"(b:{ident(b_label)} {{{ident(b_key)}: {literal(b_value)}}}) "
                f"CREATE (a)-{body}->(b);"
            )

        # Uniqueness is enforced after the load so the data itself validates it.
        for label, key in sorted(keys.items()):
            emit(f"CREATE CONSTRAINT ON (n:{ident(label)}) ASSERT n.{ident(key)} IS UNIQUE;")

        for label in fallback:
            emit(f"MATCH (n:{ident(label)}) REMOVE n.{ident(TEMP_KEY)};")
            emit(f"DROP INDEX ON :{ident(label)}({ident(TEMP_KEY)});")

    print(f"{len(nodes)} nodes, {len(relationships)} relationships -> {args.out}")
    print("keys: " + ", ".join(f":{l}({k})" for l, k in sorted(keys.items())))
    if fallback:
        print("no uniqueness constraint, used temporary _nid: " + ", ".join(fallback))


if __name__ == "__main__":
    main()
