# Phase 1 Report: Neo4j (System X)

## 1. Introduction


- [x] Who created Neo4j?
  - Emil Eifrem, Johan Svensson, and Peter Neubauer, in Sweden. [2]
  - The company was first called Neo Technology and was renamed Neo4j, Inc. on July 27, 2017. [1]
  - Name: "Neo" means "new" and "4j" means "for Java"; the project was first called "Project Neo". [2]
- [x] When was Neo4j created?
  - 2007: The company was founded in Sweden, and Neo4j was released as open source (GPL). [2], [3]
  - February 2010: Neo4j 1.0 was released. [3]
  - 2011: The company moved its headquarters to Silicon Valley and kept engineering in Sweden. [2]
- [x] Why was Neo4j created?
  - Idea started: in 2000, on a flight to Mumbai, Eifrem sketched the first property graph model on a napkin. [4]
  - Why: the three founders were building an enterprise content management (ECM) system and "kept running up against the challenge of using an RDBMS for querying connected data." [4]
    - Before Neo4j, Eifrem was CTO at Windh Technologies, a media asset management company. [4]
  - Core idea: store relationships as first-class entities instead of rebuilding them with joins. [3]
  - Location: the company has its roots in Malmö, Sweden, where its development office still is. Today it is headquartered in San Mateo, California. [5]

## 2. Data Model Fit

### Background: data models in the DBMS landscape (brief intro for the report)

The paper groups non-relational data models into eight categories. [7]

| Model                       | Shape of the data                                                  | Example systems                     | Paper's view                                                                  |
| --------------------------- | ------------------------------------------------------------------ | ----------------------------------- | ----------------------------------------------------------------------------- |
| Relational (RM)             | Tables of rows and columns, queried with SQL                       | MySQL, PostgreSQL, Oracle           | Dominant; most other models converge back towards it                          |
| MapReduce                   | No fixed model; user-written Map and Reduce functions over files   | Google MR, Hadoop                   | "Hadoop died about a decade ago"; replaced by Spark, Flink                    |
| Key/value                   | `(key, value)`; value is an opaque blob; get/set/delete            | Memcached, Redis, DynamoDB, RocksDB | Good for caching and sessions; "a bad idea" when records need multiple fields |
| Document                    | Nested field/value hierarchies (JSON)                              | MongoDB                             | Nearly all added SQL and ACID; "will soon be effectively identical" to RDBMSs |
| Column-family (wide-column) | Records keyed by ID with optional attributes; one level of nesting | BigTable, Cassandra, HBase          | Niche; lacks joins and secondary indexes                                      |
| Text search                 | Inverted indexes over tokenised words                              | Elasticsearch, Solr (Lucene)        | Limited transactions; RDBMSs now offer full-text search                       |
| Array                       | Multi-dimensional arrays (matrices, tensors)                       | Rasdaman, kdb+, SciDB, TileDB       | Niche: genomics, satellite and scientific data                                |
| Vector                      | One-dimensional embeddings for similarity search                   | Pinecone, Milvus, Weaviate          | RDBMSs are quickly adding vector indexes                                      |
| **Graph**                   | **Nodes and edges**                                                | **Neo4j**, TigerGraph, JanusGraph   | "We do not expect specialized graph DBMSs to be a large market"               |

- Two ways to represent graphs: [7]
  - **RDF** (triplestores): a directed graph with labelled edges only (subject → predicate → object).
  - **Property graph**: a directed multi-graph with key/value properties on nodes **and** edges. It is more common, and the paper calls it "a superset of RDF".
- Neo4j uses the **property graph** model.

### Mental model: MySQL → Neo4j (using the el_paso_airport schema)

One-line summary: *labels are like table names, except a node can have several; a relationship is a foreign key or a pivot-table row promoted to a real object that can carry its own columns.*

| MySQL | Neo4j | Note |
|---|---|---|
| Table name (`pilots`) | Label (`:Pilot`) | A node may have **zero or more** labels. [6] |
| Row | Node | |
| Column | Property | Properties live on nodes **and** relationships. [6] |
| Foreign key column (`trips.AirCraftID`) | Relationship `(:Trip)-[:USES]->(:Aircraft)` | A single FK becomes a relationship too, not only pivot tables. |
| Pivot table without extra columns (`flies`) | Relationship `(:Pilot)-[:FLIES]->(:Trip)` | The table disappears entirely. |
| Pivot table with extra columns (`works(StaffID, DepartmentID, Year)`) | `(:Staff)-[:WORKS_IN {year: 2019}]->(:Department)` | Extra columns become **relationship properties**. |
| `booking(PassengerID, TripID, BookDate, BookTime)` | `(:Passenger)-[:BOOKED {bookDate, bookTime}]->(:Trip)` | Composite PK = the relationship itself. |
| Subtype tables / `UNION` | Multiple labels (`:Person:Pilot`, `:Person:Staff`) | `MATCH (person:Person)` then matches both. |

- Asymmetry worth stating in the report: a node can carry **many labels**, but a relationship has **exactly one type**, plus a direction, a start node, and an end node. [6]
- Concrete payoff in this schema: `pilots` and `staff` repeat the same nine columns (`FirstName`, `LastName`, `Birthdate`, `Address`, `Email`, `PhoneNumber`, `Gender`, `Salary`, `HiredDate`). A shared `:Person` label removes that duplication and simplifies quiz Q7 (staff and pilots sharing a first name), which in SQL needs a join across the two tables.
- Open modelling question for Phase 2: if a booking later needs its own data (seat number, fare paid, check-in time, cancellation status), does it stay a relationship or become a `(:Booking)` node between passenger and trip? This is the graph version of "when does a link table become an entity".

- [x] What data model assumptions does Neo4j make? (document, wide-column, graph, time-series, relational, streaming, etc.)
  - Data model: **property graph**, a directed multi-graph with key/value properties on nodes and edges. [6], [7]
  - Building blocks: [6]
    - Node: an entity (e.g., a person). Classified by zero or more labels (e.g., `:User`).
    - Relationship: a connection that must have a start node, an end node, a direction, and exactly one type (e.g., `FOLLOWS`).
    - Property: key-value pairs stored on nodes **and** relationships.
  - Relationships are first-class, stored data, not rebuilt with joins at query time. [3]
  - Flexible schema: not restricted to a pre-defined model; the design can change over time. [6]
  - Index-free adjacency: "each node directly references its adjacent (neighboring) nodes", so a traversal is a pointer lookup (O(1)), versus an index lookup (O(log n)) in non-native systems. [8]
    - The paper compares this to CODASYL-style pointers; good for "traversing long edge chains", where an RDBMS "has to do this via joins." [7]
  - Workload focus: operational / OLTP (transactional). The paper calls Neo4j "the most popular graph DBMS for OLTP applications." [7]
- [x] What types of applications does this data model naturally support well?
  - Rule of thumb: the answer is found **between** records (in the connections), not **inside** them.
  - Questions that "revolve around the relationship between elements": navigating hierarchies, discovering hidden connections, analysing networks (finance, retail, government). [6]
  - Social networks ("likes", "friend-of"), knowledge graphs. [7]
  - Long chains of relationships / many hops (e.g., friends-of-friends), because traversal cost depends on the data touched, "no matter the total size of your data." [8]
  - Examples to back up in Section 4: fraud detection (rings of accounts sharing phones/addresses/devices), recommendations ("because you watched X").
- [x] What types of applications does this data model struggle with, and why?
  - Apps without "long chain" relationship queries. The paper argues Neo4j's value depends on "whether there are enough 'long chain' scenarios that merit forgoing a RDBMS." [7]
    - E.g., reports and aggregations (monthly statements, sales totals) and simple CRUD, where the answer is inside the rows.
  - A graph can be stored as relational tables (`Node`, `Edge`), so an RDBMS is "always an option". Studies show RDBMSs can outperform graph DBMSs; SQL/PGQ in DuckDB beat "a leading graph DBMS by up to 10×". [7]
  - Large-scale graph analytics: the paper says these have unique requirements "best done in main memory with specialized data structures." [7]
  - Dense nodes / "supernodes" (one node with a huge number of relationships) make retrieval expensive; the fix is remodelling with intermediate nodes. [9]
  - Horizontal scaling: splitting a connected graph across machines is hard. Neo4j "had previously struggled with scalability" and added property sharding in 2025 (in Infinigraph, its self-managed offering). [10]
    - TODO (Section 6): check which features are missing from the free Community Edition.

## 3. Query Language and Interface

- [x] What query or programming languages does Neo4j support?
  - **Cypher**: "Neo4j's declarative graph query language", "created in 2011 by Neo4j engineers as an SQL-equivalent language for graph databases." [11]
    - Neo4j launched the open **openCypher** initiative in October 2015. [12]
  - **GQL** (ISO/IEC 39075), an ISO standard property graph query language, published April 11, 2024. [12], [17]
    - "The first time in more than 35 years that ISO will be releasing a new database query language" (SQL was first published in 1987). [17]
    - GQL "subsumes many features of" openCypher (Neo4j), GSQL (TigerGraph), PGQL (Oracle), and G-Core (LDBC). [17]
    - GQL shares its Cypher-derived pattern-matching sub-language with SQL/PGQ. [17]
    - Cypher "accommodates most mandatory GQL features and a substantial portion of its optional ones", but a few mandatory features are still missing, so it is not yet fully conformant. [13]
  - Programming languages: official drivers for **Java, Python, JavaScript, .NET, and Go**, which connect over Neo4j's **Bolt** protocol. [14]
- [x] Does Neo4j support SQL or a SQL-like interface?
  - Not natively; the native language is Cypher. But Cypher was designed as an "SQL-equivalent" language, so it feels SQL-like (`WHERE`, `ORDER BY`, `LIMIT`, aggregations). [11]
  - **SQL-to-Cypher translation** is "an optional feature" of the Neo4j JDBC driver (`enableSQLTranslation=true`). [15]
    - Limitations: outer joins are not supported, and mapping tables to graphs is "an opinionated task, as there is no right way to map table names to graph objects." [15]
  - **BI Connector**: lets SQL/JDBC tools (e.g., Tableau) read Neo4j by exposing a virtual table per node label and per relationship. It is **read-only**. [16]
- [x] If Neo4j uses its own query language or API, what are the key differences compared to SQL?
  - **Patterns instead of joins**: Cypher describes the shape to find, e.g. `(passenger:Passenger)-[:BOOKED]->(trip:Trip)`, instead of joining tables on keys. [11]
  - **Clause order**: SQL starts with `SELECT`; "Cypher queries end with the return clause" (`MATCH ... WHERE ... RETURN`). [11]
  - **Conciseness**: "Cypher queries are often more concise than their equivalent SQL queries." [11]
  - **Schema flexibility**: nodes don't need the same set of properties unless a constraint says so. [11]
  - **Variable-length paths**: Cypher can say "1 to 10 hops" directly (`*1..10`); SQL needs a recursive CTE or many self-joins.
  - Paper's view: "vanilla" SQL "is not expressive enough for graph queries", but SQL:2023 added **SQL/PGQ**, which "further narrows the functionality difference between RDBMSs and native graph DBMSs." [7]

### Worked examples: el_paso airline database (MySQL) vs Neo4j (Cypher)

> Status: the SQL comes from `quiz_1.sql` (DBMS-1 individual project) and was **run against the live `el_paso_airport` database** (MySQL 8.4). The Cypher is **written by hand and not yet tested**; run it in Phase 2 and correct anything that fails.

**Actual schema** (`el_paso_airport`, verified with `DESCRIBE`) and dataset size:

| Table | Key columns | Rows |
|---|---|---|
| `aircrafts` | AircraftID, Name, NumSeats, ManufacturedDate, LastServiceDate | 20 |
| `booking` | **PassengerID + TripID** (composite PK), BookDate, BookTime | 500 |
| `departments` | DeptID, Name, Category | 7 |
| `flies` | **PilotID + TripID** (composite PK) | 200 |
| `passengers` | PassID, FirstName, LastName, Birthdate, Address, Email, PhoneNumber, RewardPoints, Citizenship, Gender | 50 |
| `pilots` | PilotID, FirstName, LastName, Birthdate, …, Salary, HiredDate, Rating | 20 |
| `services` | ServiceID, StaffID, Staff2ID, AirCraftID, ServiceDate, Description | 100 |
| `staff` | StaffID, FirstName, LastName, …, Salary, DepartmentID, HiredDate, PositionName | 20 |
| `trips` | TripID, AirCraftID, Capacity, DeptTown, DeptDate, DeptTime, ArrTown, ArrDate, ArrTime, Fare | 200 |
| `works` | **StaffID + DepartmentID** (composite PK), Year | 20 |

Note for Phase 2: this dataset is tiny (500 rows at most), so it will not show performance differences. Generate a larger synthetic dataset for the experiments.

**Graph model for the same data** (one node per entity, one relationship per join table):

```
(:Passenger {passID, firstName, lastName, birthdate, email, phoneNumber, rewardPoints, citizenship, gender})
(:Trip {tripID, capacity, deptTown, deptDate, deptTime, arrTown, arrDate, arrTime, fare})
(:Pilot {pilotID, firstName, lastName, birthdate, phoneNumber, salary, hiredDate, rating})
(:Staff {staffID, firstName, lastName, birthdate, phoneNumber, salary, hiredDate, positionName})
(:Department {deptID, name, category})
(:Aircraft {aircraftID, name, numSeats, manufacturedDate, lastServiceDate})
(:Service {serviceID, serviceDate, description})

(:Passenger)-[:BOOKED {bookDate, bookTime}]->(:Trip)   // replaces the booking table
(:Pilot)-[:FLIES]->(:Trip)                             // replaces the flies table
(:Staff)-[:WORKS_IN {year}]->(:Department)             // replaces the works table
(:Trip)-[:USES]->(:Aircraft)                           // replaces trips.AirCraftID
(:Service)-[:PERFORMED_ON]->(:Aircraft)                // replaces services.AirCraftID
(:Staff)-[:PERFORMED]->(:Service)                      // replaces services.StaffID and Staff2ID
```

Modelling points worth making in the report:
- A composite primary key (`booking`, `flies`, `works`) exists only to express a many-to-many link. In the graph it disappears: the link **is** the relationship.
- `services.StaffID` and `services.Staff2ID` are two columns for "the staff who did this service". In a graph this is one relationship type used twice, so adding a third technician needs no schema change.

Key point: every join table (`booking`, `flies`, `works`) becomes a **relationship**, and its extra columns (e.g. `BookDate`) become **relationship properties**.

#### A. Basic CRUD

| Operation | MySQL | Cypher |
|---|---|---|
| Create | `INSERT INTO passengers (PassID, FirstName, LastName, RewardPoints) VALUES (1, 'James', 'Bond', 2500);` | `CREATE (:Passenger {passID:1, firstName:'James', lastName:'Bond', rewardPoints:2500});` |
| Read | `SELECT FirstName, LastName FROM passengers WHERE RewardPoints > 2000;` | `MATCH (passenger:Passenger) WHERE passenger.rewardPoints > 2000 RETURN passenger.firstName, passenger.lastName;` |
| Update | `UPDATE passengers SET RewardPoints = 3000 WHERE PassID = 1;` | `MATCH (passenger:Passenger {passID:1}) SET passenger.rewardPoints = 3000;` |
| Delete | `DELETE FROM passengers WHERE PassID = 1;` | `MATCH (passenger:Passenger {passID:1}) DETACH DELETE passenger;` |
| Insert-or-update | `INSERT ... ON DUPLICATE KEY UPDATE ...` | `MERGE (passenger:Passenger {passID:1}) SET passenger.rewardPoints = 3000;` |
| Link two records | `INSERT INTO booking (PassengerID, TripID, BookDate) VALUES (1, 55, '2019-07-04');` | `MATCH (passenger:Passenger {passID:1}), (trip:Trip {tripID:55}) CREATE (passenger)-[:BOOKED {bookDate: date('2019-07-04')}]->(trip);` |

Points to make in the report:
- `DETACH DELETE` deletes a node **and its relationships** in one step. MySQL needs `ON DELETE CASCADE` or several statements.
- Creating a link is `CREATE (passenger)-[:BOOKED]->(trip)`; there is no foreign key and no join table.
- `MERGE` is "match or create", which has no single-statement SQL equivalent that is portable.

#### B. Simple query (quiz Q1): staff whose position is "treasurer"

```sql
select FirstName, LastName, StaffID from staff where PositionName = 'treasurer';
```
```cypher
MATCH (staff:Staff {positionName: 'treasurer'})
RETURN staff.firstName, staff.lastName, staff.staffID;
```
Verdict: near-identical. **No graph advantage** when the answer is inside one table.
MySQL result (3 rows): Kara Leon (193514918), Gabriella Hardy (473287624), Gloria Brown (648060877).

#### C. One join (quiz Q2): trips booked by "James Bond" in July 2019

```sql
select distinct booking.TripID
from booking
where booking.BookDate >= '2019-07-01' and booking.BookDate < '2019-08-01'
  and booking.PassengerID in (select passengers.PassID from passengers
                              where passengers.FirstName = 'James' and passengers.LastName = 'Bond');
```
```cypher
MATCH (passenger:Passenger {firstName:'James', lastName:'Bond'})-[booking:BOOKED]->(trip:Trip)
WHERE booking.bookDate >= date('2019-07-01') AND booking.bookDate < date('2019-08-01')
RETURN DISTINCT trip.tripID;
```
Verdict: the sub-query disappears. The filter on `bookDate` sits **on the relationship**.

**Three ways to filter in Cypher** (no SQL equivalent for the first two):

1. **Property map inside the pattern** — equality only. A map "can be added to filter on properties of the relationship based on equality". [18]
   ```cypher
   MATCH (passenger:Passenger {firstName:'James', lastName:'Bond'})-[booking:BOOKED {bookDate: date('2019-07-04')}]->(trip:Trip)
   ```
2. **Inline `WHERE` inside the brackets** — any predicate, on nodes (`MATCH (station:Station WHERE station.name STARTS WITH 'Preston')`) and on relationships (`-[r WHERE time() + duration(r.duration) < time('22:00')]->`). [18]
   ```cypher
   MATCH (passenger:Passenger {firstName:'James', lastName:'Bond'})
         -[booking:BOOKED WHERE booking.bookDate >= date('2019-07-01') AND booking.bookDate < date('2019-08-01')]->
         (trip:Trip)
   RETURN DISTINCT trip.tripID;
   ```
3. **Separate `WHERE` clause** — the familiar SQL-like form, used in the query above.

Report point: in SQL every filter goes in one `WHERE` clause, far from the join that produced the row. Cypher lets the filter sit **on the element it constrains**, inside the pattern. Verify in Phase 2 which Neo4j version is used, since relationship pattern predicates are a newer addition.

#### D. Aggregation (quiz Q3): pilots who flew exactly once in July 2019

```sql
select pilots.FirstName, pilots.LastName, pilots.PhoneNumber
from pilots
where pilots.PilotID in (select flies.PilotID from flies
                         where flies.TripID in (select trips.TripID from trips
                                                where trips.DeptDate >= '2019-07-01' and trips.DeptDate < '2019-08-01')
                         group by flies.PilotID having count(flies.TripID) = 1);
```
```cypher
MATCH (pilot:Pilot)-[:FLIES]->(trip:Trip)
WHERE trip.deptDate >= date('2019-07-01') AND trip.deptDate < date('2019-08-01')
WITH pilot, count(DISTINCT trip) AS tripCount
WHERE tripCount = 1
RETURN pilot.firstName, pilot.lastName, pilot.phoneNumber;
```
Verdict: `WITH` is Cypher's pipeline step; it does the job of `GROUP BY ... HAVING`. Note that Cypher groups **implicitly** by the non-aggregated variables, so there is no `GROUP BY` clause.

#### E. Two hops (quiz Q4): passengers to El Paso with more than 2000 reward points

```sql
select passengers.FirstName, passengers.LastName, passengers.RewardPoints
from passengers
where passengers.RewardPoints > 2000
  and passengers.PassID in (select booking.PassengerID from booking
                            where booking.TripID in (select trips.TripID from trips
                                                     where trips.ArrTown = 'El Paso'))
order by passengers.RewardPoints;
```
```cypher
MATCH (passenger:Passenger)-[:BOOKED]->(:Trip {arrTown:'El Paso'})
WHERE passenger.rewardPoints > 2000
RETURN DISTINCT passenger.firstName, passenger.lastName, passenger.rewardPoints
ORDER BY passenger.rewardPoints;
```
Verdict: two nested sub-queries collapse into one pattern.
MySQL result: 12 passengers, from Raul Dee (2178 points) to Jose Gusler (4879).

#### F. Deep chain (quiz Q10): services on aircraft flown by pilot "Parth Nagarkar"

```sql
select count(distinct services.ServiceID)
from services
where services.AirCraftID in (select trips.AirCraftID from trips
                              where trips.TripID in (select flies.TripID from flies
                                                     where flies.PilotID in (select pilots.PilotID from pilots
                                                                             where pilots.FirstName = 'Parth' and pilots.LastName = 'Nagarkar')));
```
```cypher
MATCH (:Pilot {firstName:'Parth', lastName:'Nagarkar'})-[:FLIES]->(:Trip)-[:USES]->(aircraft:Aircraft)<-[:PERFORMED_ON]-(service:Service)
RETURN count(DISTINCT service.serviceID);
```
Verdict: **the best example for the report.** Four levels of nested sub-queries become one line that reads like the path itself: pilot → trip → aircraft → service.
MySQL result: 28. Check that the Cypher returns 28 as well in Phase 2.

#### F2. Natural join (quiz Q9): pilots on high-capacity trips departing from a town starting with "S"

```sql
select LastName, TripID, ArrTown
from trips natural join flies natural join pilots
where Capacity > 450 and DeptTown like 'S%';
```
```cypher
MATCH (pilot:Pilot)-[:FLIES]->(trip:Trip)
WHERE trip.capacity > 450 AND trip.deptTown STARTS WITH 'S'
RETURN pilot.lastName, trip.tripID, trip.arrTown;
```
MySQL result (6 rows): Wollenberg, Hess, Ledbetter, Murphy, Hess, Schmidt.
Verdict: `NATURAL JOIN` silently joins on **every** column that shares a name, which is risky. Cypher names the relationship explicitly, so the link is never guessed. Also note `STARTS WITH` in place of `LIKE 'S%'`.

#### G. What SQL cannot express as easily: variable-length paths

There is no quiz equivalent, because the schema has no recursive relationship. Example question: "which trips are reachable from this one by transfers, up to 5 legs?"

```cypher
MATCH path = (startTrip:Trip {tripID:55})-[:CONNECTS*1..5]->(destinationTrip:Trip)
RETURN destinationTrip.tripID, length(path);
```
In MySQL this needs a recursive CTE (`WITH RECURSIVE`), and the query grows much more complex as the depth increases. This is the difference to highlight in Section 7 and to measure in Phase 2.

TODO for Phase 2: load this airline data into Neo4j, run each query above, and record the actual results and timings.

## 4. Use Cases and Practical Applications

- [x] What are the primary use cases of Neo4j?
  - Principle first: Neo4j suits applications whose central question is about the relationships **between** records, not the attributes **within** a record. The same data can be asked both kinds of question, so this is about the query, not the dataset.
- [x] Are there any companies using Neo4j?
  - Yes. The table below pairs each use case with a documented deployment.

| Use case | The question being asked | Company (documented) | What they built |
|---|---|---|---|
| Investigative / financial-crime search | "Who is secretly connected to this person or company?" | **ICIJ** (Panama Papers) [19] | Analysed 11.5 million leaked documents covering 40 years and 200,000+ companies in 21 tax havens; won the Pulitzer Prize |
| Compliance / beneficial ownership | "Who ultimately owns this company, through any chain?" | **Dun & Bradstreet** [23] | A B2B compliance service mapping shareholder interests through "complex inter-company dependencies"; replaced hours of manual research with "millisecond queries" |
| Data lineage / risk reporting | "Where did this number come from, and what breaks if it changes?" | **UBS** [24] | The Group Data Dictionary, built for BCBS 239 compliance, after an Oracle-based first version hit limits |
| Knowledge graph / AI assistant | "What does this shopper actually mean?" | **eBay** [21] | The eBay App on Google Assistant: a conversational shopping bot backed by a Neo4j knowledge graph |
| Knowledge management | "Which past lessons are relevant to my current problem?" | **NASA** [20] | Rebuilt the Lessons Learned Information System as a knowledge graph |
| Master data management / data discovery | "Which dataset should I trust, and who owns it?" | **Airbnb** [22] | The Dataportal, connecting 200,000+ Hive tables, 10,000 charts and 6,000 Tableau workbooks for 3,500 employees |

  - Other use cases commonly cited but **not yet sourced here**: real-time recommendations, network and IT operations (dependency mapping), identity and access management, supply chain traceability, social networks. Find citations before using these in the report.
- [x] How and why does Neo4j work better for their use cases compared to traditional relational databases?
  - **UBS — joins were the bottleneck.** "The first iteration was built on Oracle, but UBS soon discovered limitations with an RDBMS approach, which relies on JOINS to connect data across tables." [24] This is the strongest evidence in the report: a company that tried relational first and moved.
  - **NASA — keyword search cannot follow connections.** An engineer searching with 23 keywords received about 10 million documents with no sense of relevance. After the move, an Orion engineer found an Apollo-era lesson that "prevented an issue and saved … well over two years of work and one million dollars of taxpayer funds." [20]
  - **ICIJ — exploration, not one-shot queries.** "You could enter a name and just double click, and the networks would expand." — Mar Cabra, ICIJ Data Editor. [19] The value is following links interactively, which is awkward in SQL because each hop is another join written by hand.
  - **Dun & Bradstreet — unbounded depth.** Ownership chains have no fixed length, so the SQL would need recursion; Neo4j answers in milliseconds. [23]
  - **eBay and Airbnb — context and discovery.** Both connect items that live in separate systems (products and intent; tables, dashboards and owners) where the useful answer is the path between them. [21], [22]
  - Caveat to state in the report: items [19], [21], [22], [23] and [24] are **Neo4j's own case studies**, so they are vendor-published and favourable by design. [20] (diginomica) is independent journalism. Balance them with the critical view in [7].

## 5. Competitors and Market Analysis

- [ ] Conduct a literature survey of academic and industry sources to identify competitors.
- [ ] How is Neo4j different from its competitors?
- [ ] What is novel or unique about Neo4j compared to existing systems?

## 6. Drawbacks and Limitations

- [x] What are the major drawbacks or limitations of Neo4j?
  - **Community Edition is heavily restricted.** Enterprise-only features include clustering, composite databases, online backup and restore, **multiple databases beyond `system` and the default**, role-based and property-based access control, and LDAP/Active Directory integration. [25]
    - Verified on our own machine (Neo4j 2026.09.0 Community, installed via Homebrew):
      ```
      CREATE DATABASE elpaso;
      → 51N27: system configuration or operation exception - not supported in this edition.
        'CREATE DATABASE' is not supported in community edition.
      ```
    - Licensing: Community is GPLv3; Enterprise needs a commercial licence. [25], [26]
    - Practical effect for a student or small team: no online backup, no user roles, and one user database per server instance.
  - **Horizontal scaling is hard.** Splitting a connected graph across machines breaks the pointers that make traversal fast. Neo4j "had previously struggled with scalability" and only added property sharding in 2025, in Infinigraph. [10]
  - **Supernodes / dense nodes.** A node with a very large number of relationships becomes expensive to traverse; the fix is remodelling with intermediate nodes. [9]
  - **No advantage without "long chains".** The paper's central objection: a graph can be simulated with `Node` and `Edge` tables, so an RDBMS is "always an option", and adoption "comes down to whether there are enough 'long chain' scenarios that merit forgoing a RDBMS." [7]
  - **Relational systems are catching up.** SQL:2023 added SQL/PGQ, which "further narrows the functionality difference"; DuckDB with SQL/PGQ outperformed "a leading graph DBMS by up to 10×", and other studies show RDBMSs beating graph DBMSs on graph simulation. [7]
  - **Weak fit for analytics.** Large-scale graph analytics is "best done in main memory with specialized data structures", not in a general graph DBMS. [7]
  - **Learning curve and ecosystem.** Cypher is a second language for a SQL team, and the surrounding tool ecosystem (BI, reporting, ORMs) is smaller: Neo4j's own BI Connector is read-only, and its SQL translation does not support outer joins. [15], [16]
- [x] In what scenarios would you not recommend using Neo4j?
  These are limits of the **graph model itself**, not of a particular edition.

  1. **Set-oriented and aggregate workloads.** "Total revenue per month", dashboards, OLAP roll-ups. These scan whole collections, so index-free adjacency buys nothing and row/columnar storage wins. [7]
  2. **Weakly connected or uniform tabular data.** If records rarely reference each other, or every query touches one entity type, the graph stores relationships that no query ever traverses: extra storage and modelling effort for no benefit.
  3. **Shallow, fixed-depth joins.** One or two hops known at design time is exactly what the relational optimiser is built for; the traversal advantage only appears as depth grows and becomes variable. [7]
  4. **High-volume write/ingest streams.** Logs, sensor readings, clickstreams: each relationship is a stored object with pointers to maintain, which is overhead on data that is written once and aggregated later.
  5. **Workloads requiring horizontal partitioning.** Graphs resist being cut apart because edges cross any partition boundary. The paper notes distributed graph algorithms "rarely outperform single-node implementations because of communication costs", and recommends compressing the graph to fit in memory on one machine instead. [7]
  6. **Large-scale graph analytics** (PageRank, community detection, shortest path over the whole graph). The paper argues these "have unique requirements that are best done in main memory with specialized data structures", not in an operational graph DBMS. [7]
  7. **Graphs with a heavily skewed degree distribution.** Supernodes (one node holding a huge share of the relationships) make traversal costs explode and force remodelling. [9]
  8. **Strict, database-enforced integrity.** The property graph model is schema-optional by design. Cypher offers uniqueness constraints; property existence, property type and key constraints exist too, and **there are no CHECK constraints and no foreign-key referential integrity** as SQL understands them. [27] If the database must be the last line of defence for data rules, relational is stronger.
  9. **When the RDBMS can already express the query.** A graph can be simulated with `Node` and `Edge` tables, so an RDBMS is "always an option"; with recursive CTEs and SQL:2023's SQL/PGQ, adding a second system may not be justified. [7]
  10. **Ecosystem and portability constraints.** SQL is universally supported by BI, reporting and ORM tooling and has been standard since 1987; GQL only became an ISO standard in 2024, so graph tooling and portability are less mature. [17]

  - One-line summary for the report: *do not use a graph database when the questions are aggregates within records rather than paths between them, when the data is barely connected, when the workload must be partitioned across many machines, or when the database itself must enforce strict integrity rules.*
  - Edition-specific limits (clustering, backups, multiple databases, RBAC) are separate from the model and are covered in the drawbacks list above.

## 7. Phase 2 Readiness Reflection

- [ ] Based on your analysis, why is Neo4j a good (or challenging) candidate to compare against MySQL in Phase 2?
- [ ] What differences do you expect to observe when comparing Neo4j with MySQL experimentally?
- [ ] Make sure the answers fit a system you can realistically install and experiment with in Phase 2.

---

## References

1. E. Eifrem, "Hello, world: Neo4j, Inc.," Neo4j Blog, Jul. 27, 2017. https://neo4j.com/blog/emil-eifrem/hello-world-neo4j-inc/
2. "Neo4j," Wikipedia. https://en.wikipedia.org/wiki/Neo4j
3. "Neo4j," Database of Databases, Carnegie Mellon University. https://dbdb.io/db/neo4j
4. D. Baer, "'Something much broader than compensation drives us.' – An Interview with Emil Eifrem, Neo4j Founder," HackerNoon, Jun. 28, 2019. https://hackernoon.com/something-much-broader-than-compensation-drives-us-emil-eifrem-neo4j-founder-interview-168278957077
5. "Ola Rollén invests in Malmö-founded Neo Technology," Øresund Startups, Nov. 7, 2016. https://oresundstartups.com/ola-rollen-invests-malmo-founded-neo-technology/
6. Neo4j, "What is a graph database," Neo4j Getting Started Guide. https://neo4j.com/docs/getting-started/graph-database/
7. M. Stonebraker and A. Pavlo, "What Goes Around Comes Around... And Around...," SIGMOD Record, vol. 53, no. 2, pp. 21–37, Jun. 2024. https://db.cs.cmu.edu/papers/2024/whatgoesaround-sigmodrec2024.pdf
8. J. Stegeman, "Native vs. non-native graph database," Neo4j Blog, May 8, 2023. https://neo4j.com/blog/cypher-and-gql/native-vs-non-native-graph-technology/
9. Neo4j, "Modeling designs," Neo4j Getting Started Guide. https://neo4j.com/docs/getting-started/data-modeling/modeling-designs/
10. L. Clark, "Neo4j intros 'property sharding' to tackle scalability," The Register, Sep. 11, 2025. https://www.theregister.com/2025/09/11/neo4j_property_sharding_to_address_scalability_challenge/
11. Neo4j, "Cypher overview," Neo4j Cypher Manual. https://neo4j.com/docs/cypher-manual/current/introduction/cypher-overview/
12. V. Malenchino, "openCypher will pave the road to GQL for Cypher implementers," Neo4j Blog, May 22, 2024. https://neo4j.com/blog/cypher-and-gql/opencypher-gql-cypher-implementation/
13. Neo4j, "GQL conformance," Neo4j Cypher Manual. https://neo4j.com/docs/cypher-manual/current/appendix/gql-conformance/
14. Neo4j, "Neo4j drivers," Bolt Protocol Documentation. https://neo4j.com/docs/bolt/current/neo4j-drivers/
15. Neo4j, "SQL to Cypher translation," Neo4j JDBC Driver Manual. https://neo4j.com/docs/jdbc-manual/current/sql2cypher/
16. D. Allen and L. Smith, "Getting started with the Neo4j BI Connector," Neo4j Blog, Mar. 24, 2020. https://neo4j.com/blog/cypher-and-gql/getting-started-with-the-neo4j-bi-connector/
17. K. W. Hare, "ISO/IEC 39075 Database Language GQL," ISO/IEC JTC 1, Apr. 2024. https://www.jtc1info.org/wp-content/uploads/2024/04/2024-Article-39075-Database-Language-GQL.docx.pdf
18. Neo4j, "Fixed-length patterns," Neo4j Cypher Manual. https://neo4j.com/docs/cypher-manual/current/patterns/fixed-length-patterns/
19. Neo4j, "Neo4j Enables Pulitzer Prize-Winning Investigation into Global Tax Evasion (ICIJ)," Neo4j Case Study. https://go.neo4j.com/rs/710-RRC-335/images/Neo4j-case-study-ICIJ-EN-US.pdf
20. J. Twentyman, "NASA explores graph database for deep insights into space," diginomica, Feb. 2, 2017. https://diginomica.com/nasa-explores-graph-database-deep-insights-space
21. Neo4j, "Neo4j Powers Intelligent Commerce for eBay App on Google Assistant," Neo4j Case Study. https://go.neo4j.com/rs/710-RRC-335/images/Neo4j_CS_eBayShopBot_EN_US.pdf
22. Neo4j, "Master Data Management Empowers a Global Network of Airbnb Employees," Neo4j Case Study. https://go.neo4j.com/rs/710-RRC-335/images/Neo4j-case-study-Airbnb-EN-US.pdf
23. Neo4j, "Dun & Bradstreet Chooses Neo4j to Accelerate Clients' Compliance with Complex Company Ownership Checks," Neo4j Case Study. https://go.neo4j.com/rs/710-RRC-335/images/Neo4j-case-study-Dun-Bradstreet-EN-A4.pdf
24. Neo4j, "UBS: Data Lineage Tool Improves Risk Management, Drives Compliance," Neo4j Case Study. https://go.neo4j.com/rs/710-RRC-335/images/Neo4j-case-study-UBS-EN-US.pdf
25. Neo4j, "Introduction: Neo4j editions," Neo4j Operations Manual. https://neo4j.com/docs/operations-manual/current/introduction/
26. Neo4j, "FAQ: Neo4j Enterprise Edition is moving to an open core licensing model." https://neo4j.com/open-core-and-neo4j/
27. Neo4j, "Constraints," Neo4j Cypher Manual. https://neo4j.com/docs/cypher-manual/current/constraints/
