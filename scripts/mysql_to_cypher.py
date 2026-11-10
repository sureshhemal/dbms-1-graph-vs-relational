#!/usr/bin/env python3
"""Export the el_paso_airport MySQL database as Cypher for Neo4j.

Reads every table through the mysql client and writes CREATE statements that
build the equivalent property graph:

    (:Passenger)-[:BOOKED {bookDate, bookTime}]->(:Trip)
    (:Pilot)-[:FLIES]->(:Trip)
    (:Trip)-[:USES]->(:Aircraft)
    (:Staff)-[:WORKS_IN {year}]->(:Department)      # works table
    (:Staff)-[:ASSIGNED_TO]->(:Department)          # staff.DepartmentID column
    (:Staff)-[:PERFORMED]->(:Service)               # services.StaffID and Staff2ID
    (:Service)-[:PERFORMED_ON]->(:Aircraft)

Usage:
    python3 mysql_to_cypher.py > el_paso_airport.cypher
"""

import subprocess
import sys

MYSQL = "/usr/local/mysql/bin/mysql"
DB = "el_paso_airport"
USER = "root"
PASSWORD = "password"  # local dev only; override with --password


def query(sql):
    """Run SQL and return rows as lists of strings ('NULL' for null)."""
    out = subprocess.run(
        [MYSQL, "-u", USER, f"-p{PASSWORD}", "-N", "-B", "--raw", DB, "-e", sql],
        capture_output=True,
        text=True,
    )
    if out.returncode:
        sys.exit(f"mysql failed: {out.stderr.strip()}")
    return [line.split("\t") for line in out.stdout.splitlines()]


def esc(value):
    """Quote a value as a Cypher literal."""
    if value == "NULL":
        return "null"
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def date(value):
    return "null" if value == "NULL" else f"date({esc(value)})"


def time_(value):
    return "null" if value == "NULL" else f"time({esc(value)})"


def num(value):
    return "null" if value == "NULL" else value


def props(pairs):
    return "{" + ", ".join(f"{k}: {v}" for k, v in pairs) + "}"


NODES = [
    # label, table, columns, builder returning property pairs
    ("Passenger", "passengers",
     "PassID, FirstName, LastName, Birthdate, Address, Email, PhoneNumber, RewardPoints, Citizenship, Gender",
     lambda r: [("passID", num(r[0])), ("firstName", esc(r[1])), ("lastName", esc(r[2])),
                ("birthdate", date(r[3])), ("address", esc(r[4])), ("email", esc(r[5])),
                ("phoneNumber", esc(r[6])), ("rewardPoints", num(r[7])),
                ("citizenship", esc(r[8])), ("gender", esc(r[9]))]),
    ("Aircraft", "aircrafts",
     "AircraftID, Name, NumSeats, ManufacturedDate, LastServiceDate",
     lambda r: [("aircraftID", num(r[0])), ("name", esc(r[1])), ("numSeats", num(r[2])),
                ("manufacturedDate", date(r[3])), ("lastServiceDate", date(r[4]))]),
    ("Trip", "trips",
     "TripID, Capacity, DeptTown, DeptDate, DeptTime, ArrTown, ArrDate, ArrTime, Fare",
     lambda r: [("tripID", num(r[0])), ("capacity", num(r[1])), ("deptTown", esc(r[2])),
                ("deptDate", date(r[3])), ("deptTime", time_(r[4])), ("arrTown", esc(r[5])),
                ("arrDate", date(r[6])), ("arrTime", time_(r[7])), ("fare", num(r[8]))]),
    ("Department", "departments",
     "DeptID, Name, Category",
     lambda r: [("deptID", num(r[0])), ("name", esc(r[1])), ("category", esc(r[2]))]),
    ("Staff", "staff",
     "StaffID, FirstName, LastName, Birthdate, Address, Email, PhoneNumber, Gender, Salary, HiredDate, PositionName",
     lambda r: [("staffID", num(r[0])), ("firstName", esc(r[1])), ("lastName", esc(r[2])),
                ("birthdate", date(r[3])), ("address", esc(r[4])), ("email", esc(r[5])),
                ("phoneNumber", esc(r[6])), ("gender", esc(r[7])), ("salary", num(r[8])),
                ("hiredDate", date(r[9])), ("positionName", esc(r[10]))]),
    ("Pilot", "pilots",
     "PilotID, FirstName, LastName, Birthdate, Address, Email, PhoneNumber, Gender, Salary, HiredDate, Rating",
     lambda r: [("pilotID", num(r[0])), ("firstName", esc(r[1])), ("lastName", esc(r[2])),
                ("birthdate", date(r[3])), ("address", esc(r[4])), ("email", esc(r[5])),
                ("phoneNumber", esc(r[6])), ("gender", esc(r[7])), ("salary", num(r[8])),
                ("hiredDate", date(r[9])), ("rating", num(r[10]))]),
    ("Service", "services",
     "ServiceID, ServiceDate, Description",
     lambda r: [("serviceID", num(r[0])), ("serviceDate", date(r[1])), ("description", esc(r[2]))]),
]

KEYS = {
    "Passenger": "passID", "Aircraft": "aircraftID", "Trip": "tripID",
    "Department": "deptID", "Staff": "staffID", "Pilot": "pilotID", "Service": "serviceID",
}


def main():
    out = []
    out.append("// el_paso_airport as a property graph, generated from MySQL by scripts/mysql_to_cypher.py")
    out.append("// Load with: cypher-shell -u neo4j -p password --file el_paso_airport.cypher")
    out.append("")
    out.append("MATCH (n) DETACH DELETE n;")
    out.append("")

    out.append("// --- constraints (also create the indexes used for lookups below) ---")
    for label, key in KEYS.items():
        out.append(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
                   f"FOR (node:{label}) REQUIRE node.{key} IS UNIQUE;")
    out.append("")

    for label, table, columns, build in NODES:
        rows = query(f"SELECT {columns} FROM `{table}`;")
        out.append(f"// --- {len(rows)} {label} nodes (from `{table}`) ---")
        for row in rows:
            out.append(f"CREATE (:{label} {props(build(row))});")
        out.append("")

    def rel(title, sql, pattern):
        rows = query(sql)
        out.append(f"// --- {len(rows)} {title} ---")
        for row in rows:
            out.append(pattern(row))
        out.append("")

    rel("BOOKED relationships (from `booking`)",
        "SELECT PassengerID, TripID, BookDate, BookTime FROM booking;",
        lambda r: (f"MATCH (passenger:Passenger {{passID: {num(r[0])}}}), (trip:Trip {{tripID: {num(r[1])}}}) "
                   f"CREATE (passenger)-[:BOOKED {{bookDate: {date(r[2])}, bookTime: {time_(r[3])}}}]->(trip);"))

    rel("FLIES relationships (from `flies`)",
        "SELECT PilotID, TripID FROM flies;",
        lambda r: (f"MATCH (pilot:Pilot {{pilotID: {num(r[0])}}}), (trip:Trip {{tripID: {num(r[1])}}}) "
                   f"CREATE (pilot)-[:FLIES]->(trip);"))

    rel("USES relationships (from `trips.AirCraftID`)",
        "SELECT TripID, AirCraftID FROM trips WHERE AirCraftID IS NOT NULL;",
        lambda r: (f"MATCH (trip:Trip {{tripID: {num(r[0])}}}), (aircraft:Aircraft {{aircraftID: {num(r[1])}}}) "
                   f"CREATE (trip)-[:USES]->(aircraft);"))

    rel("WORKS_IN relationships (from `works`)",
        "SELECT StaffID, DepartmentID, Year FROM works;",
        lambda r: (f"MATCH (staff:Staff {{staffID: {num(r[0])}}}), (department:Department {{deptID: {num(r[1])}}}) "
                   f"CREATE (staff)-[:WORKS_IN {{year: {num(r[2])}}}]->(department);"))

    rel("ASSIGNED_TO relationships (from `staff.DepartmentID`)",
        "SELECT StaffID, DepartmentID FROM staff WHERE DepartmentID IS NOT NULL;",
        lambda r: (f"MATCH (staff:Staff {{staffID: {num(r[0])}}}), (department:Department {{deptID: {num(r[1])}}}) "
                   f"CREATE (staff)-[:ASSIGNED_TO]->(department);"))

    rel("PERFORMED_ON relationships (from `services.AirCraftID`)",
        "SELECT ServiceID, AirCraftID FROM services WHERE AirCraftID IS NOT NULL;",
        lambda r: (f"MATCH (service:Service {{serviceID: {num(r[0])}}}), (aircraft:Aircraft {{aircraftID: {num(r[1])}}}) "
                   f"CREATE (service)-[:PERFORMED_ON]->(aircraft);"))

    rel("PERFORMED relationships (from `services.StaffID` and `services.Staff2ID`)",
        "SELECT ServiceID, StaffID FROM services WHERE StaffID IS NOT NULL "
        "UNION ALL SELECT ServiceID, Staff2ID FROM services WHERE Staff2ID IS NOT NULL;",
        lambda r: (f"MATCH (staff:Staff {{staffID: {num(r[1])}}}), (service:Service {{serviceID: {num(r[0])}}}) "
                   f"CREATE (staff)-[:PERFORMED]->(service);"))

    print("\n".join(out))


if __name__ == "__main__":
    main()
