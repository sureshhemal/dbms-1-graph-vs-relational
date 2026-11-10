#!/usr/bin/env python3
"""Generate a synthetic StackOverflow-shaped dataset at arbitrary scale.

Writes one set of CSV files that loads into both a graph database and MySQL,
plus a loader script for each:

    csv/users.csv  tags.csv  questions.csv  answers.csv  comments.csv
        question_tags.csv
    load_memgraph.cypher   -- LOAD CSV, nodes and relationships
    load_mysql.sql         -- LOAD DATA LOCAL INFILE into the same schema

The shape mirrors the real stackoverflow43 dataset so the same benchmark
queries run unchanged against both sizes: the node mix, ~2.8 tags per question,
one asker per question, comments only on questions, ~39% of questions with an
accepted answer, and a single `deleted` user whose id is a string rather than
an integer.

Counts are deterministic for a given --nodes and --seed. Activity is skewed by
a power law, so a few users, questions and tags dominate -- flat uniform data
would make every index lookup equally cheap and flatter any benchmark.

    python3 scripts/generate_dummy_data.py --nodes 1000000 --out dummy_1m
"""

import argparse
import csv
import random
from array import array
from pathlib import Path

# Node mix, taken from the real dataset's proportions. Must sum to 1.0.
MIX = {
    "questions": 0.26,
    "users": 0.22,
    "answers": 0.23,
    "comments": 0.23,
    "tags": 0.06,
}

# uuid ranges, kept disjoint per entity the way StackOverflow post ids are.
ID_BASE = {"question": 1_000_000, "answer": 5_000_000, "comment": 20_000_000}

DELETED_USER = "deleted"  # one account in the real data has a string id

# Window matching the real dataset: 2020-04-24 .. 2021-09-21.
EPOCH_FROM, EPOCH_TO = 1_587_746_198, 1_632_249_176

TAGS_PER_QUESTION = ([1] * 20) + ([2] * 25) + ([3] * 25) + ([4] * 18) + ([5] * 12)
ACCEPTED_SHARE = 0.39

WORDS = """neo4j cypher graph database query node edge relationship traversal index
constraint memgraph mysql join schema aggregate shortest path pattern match merge
driver bolt transaction commit rollback property label apoc procedure plugin cluster
replica shard latency throughput cache planner optimizer explain profile benchmark
python java javascript spring docker kubernetes import export csv json dump restore
unique duplicate orphan null type cast integer string boolean list map datetime
performance slow timeout memory heap paging batch unwind collect count sum order
limit skip distinct where return create delete detach set remove rename copy""".split()


def skewed(rnd, n, power=3.0):
    """Power-law index into [0, n): low indices are far more likely."""
    return int(n * (rnd.random() ** power))


def sentence(rnd, min_words, max_words, capitalize=True):
    count = rnd.randint(min_words, max_words)
    words = [WORDS[skewed(rnd, len(WORDS), 1.5)] for _ in range(count)]
    text = " ".join(words)
    return text[0].upper() + text[1:] if capitalize else text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, default=1_000_000, help="total node count")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--schema", default="stackoverflow_1m", help="MySQL schema name")
    args = parser.parse_args()

    rnd = random.Random(args.seed)
    out = Path(args.out).resolve()
    csv_dir = out / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)

    n = {k: int(args.nodes * share) for k, share in MIX.items()}
    n["questions"] += args.nodes - sum(n.values())  # absorb rounding
    print("nodes: " + ", ".join(f"{k}={v}" for k, v in n.items()) + f" (total {sum(n.values())})")

    q_base, a_base, c_base = ID_BASE["question"], ID_BASE["answer"], ID_BASE["comment"]

    def pick_user():
        """A user id, or the deleted account for a small slice of posts."""
        if rnd.random() < 0.004:
            return DELETED_USER
        return str(1 + skewed(rnd, n["users"] - 1))

    # --- structure first, so questions.csv can carry a truthful answer_count ---
    answer_question = array("i", (skewed(rnd, n["questions"]) for _ in range(n["answers"])))
    answer_count = array("i", bytes(4 * n["questions"]))
    first_answer = array("i", bytes(4 * n["questions"]))  # 0 = none
    for i, q in enumerate(answer_question):
        answer_count[q] += 1
        if first_answer[q] == 0:
            first_answer[q] = a_base + i

    accepted_answer = array("i", bytes(4 * n["questions"]))
    for q in range(n["questions"]):
        if first_answer[q] and rnd.random() < ACCEPTED_SHARE:
            accepted_answer[q] = first_answer[q]
    accepted_ids = frozenset(a for a in accepted_answer if a)

    comment_question = array("i", (skewed(rnd, n["questions"]) for _ in range(n["comments"])))

    def write(name, header, rows):
        path = csv_dir / name
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(header)
            count = writer.writerows(rows) or 0
        size = path.stat().st_size
        print(f"  {name:<18} {size / 1048576:>7.1f} MiB")
        return count

    write(
        "users.csv",
        ["uuid", "display_name"],
        ([str(i), f"{WORDS[i % len(WORDS)]}_{i}"] for i in range(1, n["users"]))
    )
    # The deleted account closes out the users file.
    with open(csv_dir / "users.csv", "a", encoding="utf-8", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerow([DELETED_USER, "user8996213"])

    write(
        "tags.csv",
        ["name", "link"],
        (
            [f"{WORDS[i % len(WORDS)]}-{i}", f"https://stackoverflow.com/questions/tagged/{i}"]
            for i in range(n["tags"])
        ),
    )

    def question_rows():
        for i in range(n["questions"]):
            uuid = q_base + i
            yield [
                uuid,
                sentence(rnd, 6, 12),
                sentence(rnd, 15, 60, capitalize=True),
                f"https://stackoverflow.com/q/{uuid}",
                rnd.randint(EPOCH_FROM, EPOCH_TO),
                1 + skewed(rnd, 50_000, 4.0),
                answer_count[i],
                accepted_answer[i] or "",  # empty -> NULL on load
                pick_user(),
            ]

    write(
        "questions.csv",
        [
            "uuid", "title", "body_markdown", "link", "creation_date",
            "view_count", "answer_count", "accepted_answer_id", "user_id",
        ],
        question_rows(),
    )

    def answer_rows():
        for i, q in enumerate(answer_question):
            uuid = a_base + i
            yield [
                uuid,
                sentence(rnd, 6, 12),
                sentence(rnd, 15, 50, capitalize=True),
                f"https://stackoverflow.com/a/{uuid}",
                -3 + skewed(rnd, 60, 3.0),
                1 if uuid in accepted_ids else 0,
                q_base + q,
                pick_user(),
            ]

    write(
        "answers.csv",
        ["uuid", "title", "body_markdown", "link", "score", "is_accepted",
         "question_id", "user_id"],
        answer_rows(),
    )

    write(
        "comments.csv",
        ["uuid", "link", "score", "question_id", "user_id"],
        (
            [
                c_base + i,
                f"https://stackoverflow.com/comment/{c_base + i}",
                skewed(rnd, 12, 2.5),
                q_base + q,
                pick_user(),
            ]
            for i, q in enumerate(comment_question)
        ),
    )

    def tag_rows():
        total = 0
        for i in range(n["questions"]):
            chosen = set()
            for _ in range(rnd.choice(TAGS_PER_QUESTION)):
                chosen.add(skewed(rnd, n["tags"], 3.0))
            for tag in chosen:
                total += 1
                yield [q_base + i, f"{WORDS[tag % len(WORDS)]}-{tag}"]
        print(f"  (tagged pairs: {total})")

    write("question_tags.csv", ["question_id", "tag_name"], tag_rows())

    (out / "load_mysql.sql").write_text(mysql_loader(args.schema, csv_dir))
    (out / "load_memgraph.cypher").write_text(memgraph_loader())
    print(f"\nloaders: {out / 'load_mysql.sql'}\n         {out / 'load_memgraph.cypher'}")


def mysql_loader(schema, csv_dir):
    """LOAD DATA script against the same schema as the real dataset."""
    files = {
        "users": "(`uuid`, `display_name`)",
        "tags": "(`name`, `link`)",
        "questions": (
            "(`uuid`, `title`, `body_markdown`, `link`, `creation_date`, `view_count`,\n"
            " `answer_count`, @accepted_answer_id, `user_id`)\n"
            "  SET `accepted_answer_id` = NULLIF(@accepted_answer_id, '')"
        ),
        "answers": (
            "(`uuid`, `title`, `body_markdown`, `link`, `score`, `is_accepted`,\n"
            " `question_id`, `user_id`)"
        ),
        "comments": "(`uuid`, `link`, `score`, `question_id`, `user_id`)",
        "question_tags": "(`question_id`, `tag_name`)",
    }
    loads = "\n".join(
        f"LOAD DATA LOCAL INFILE '{csv_dir}/{table}.csv'\n"
        f"  INTO TABLE `{table}`\n"
        "  FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '\"'\n"
        "  LINES TERMINATED BY '\\n' IGNORE 1 LINES\n"
        f"  {columns};\n"
        for table, columns in files.items()
    )
    return f"""\
-- Synthetic StackOverflow-shaped dataset -> MySQL.
-- Generated by scripts/generate_dummy_data.py; CSV paths are absolute, so
-- adjust them if you move the csv/ directory.
--
-- Load with (the client flag is required for LOAD DATA LOCAL INFILE):
--   mysql --local-infile=1 -u root -p < load_mysql.sql
-- If the server refuses it: SET GLOBAL local_infile = 1;

SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS;
SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS;
SET FOREIGN_KEY_CHECKS=0;
SET UNIQUE_CHECKS=0;

CREATE DATABASE IF NOT EXISTS `{schema}`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE `{schema}`;

DROP TABLE IF EXISTS `question_tags`;
DROP TABLE IF EXISTS `comments`;
DROP TABLE IF EXISTS `answers`;
DROP TABLE IF EXISTS `questions`;
DROP TABLE IF EXISTS `tags`;
DROP TABLE IF EXISTS `users`;

CREATE TABLE `users` (
  `uuid` VARCHAR(32) NOT NULL COMMENT 'numeric id, or the literal "deleted"',
  `display_name` VARCHAR(255) NOT NULL,
  PRIMARY KEY (`uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `tags` (
  `name` VARCHAR(100) NOT NULL,
  `link` VARCHAR(512) NOT NULL,
  PRIMARY KEY (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `questions` (
  `uuid` BIGINT UNSIGNED NOT NULL,
  `title` VARCHAR(255) NOT NULL,
  `body_markdown` MEDIUMTEXT NOT NULL,
  `link` VARCHAR(512) NOT NULL,
  `creation_date` BIGINT UNSIGNED NOT NULL COMMENT 'epoch seconds; FROM_UNIXTIME()',
  `view_count` INT UNSIGNED NOT NULL,
  `answer_count` INT UNSIGNED NOT NULL,
  `accepted_answer_id` BIGINT UNSIGNED DEFAULT NULL,
  `user_id` VARCHAR(32) NOT NULL COMMENT 'asker',
  PRIMARY KEY (`uuid`),
  KEY `idx_questions_user` (`user_id`),
  KEY `idx_questions_creation_date` (`creation_date`),
  KEY `idx_questions_accepted_answer` (`accepted_answer_id`),
  CONSTRAINT `fk_questions_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `answers` (
  `uuid` BIGINT UNSIGNED NOT NULL,
  `title` VARCHAR(255) NOT NULL,
  `body_markdown` MEDIUMTEXT NOT NULL,
  `link` VARCHAR(512) NOT NULL,
  `score` INT NOT NULL,
  `is_accepted` BOOLEAN NOT NULL,
  `question_id` BIGINT UNSIGNED NOT NULL,
  `user_id` VARCHAR(32) NOT NULL,
  PRIMARY KEY (`uuid`),
  KEY `idx_answers_question` (`question_id`),
  KEY `idx_answers_user` (`user_id`),
  CONSTRAINT `fk_answers_question` FOREIGN KEY (`question_id`) REFERENCES `questions` (`uuid`) ON DELETE CASCADE,
  CONSTRAINT `fk_answers_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `comments` (
  `uuid` BIGINT UNSIGNED NOT NULL,
  `link` VARCHAR(512) NOT NULL,
  `score` INT NOT NULL,
  `question_id` BIGINT UNSIGNED NOT NULL,
  `user_id` VARCHAR(32) NOT NULL,
  PRIMARY KEY (`uuid`),
  KEY `idx_comments_question` (`question_id`),
  KEY `idx_comments_user` (`user_id`),
  CONSTRAINT `fk_comments_question` FOREIGN KEY (`question_id`) REFERENCES `questions` (`uuid`) ON DELETE CASCADE,
  CONSTRAINT `fk_comments_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `question_tags` (
  `question_id` BIGINT UNSIGNED NOT NULL,
  `tag_name` VARCHAR(100) NOT NULL,
  PRIMARY KEY (`question_id`,`tag_name`),
  KEY `idx_question_tags_tag` (`tag_name`),
  CONSTRAINT `fk_question_tags_question` FOREIGN KEY (`question_id`) REFERENCES `questions` (`uuid`) ON DELETE CASCADE,
  CONSTRAINT `fk_question_tags_tag` FOREIGN KEY (`tag_name`) REFERENCES `tags` (`name`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE `questions`
  ADD CONSTRAINT `fk_questions_accepted_answer` FOREIGN KEY (`accepted_answer_id`)
  REFERENCES `answers` (`uuid`) ON DELETE SET NULL;

{loads}
SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS;
SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS;
"""


def memgraph_loader():
    """LOAD CSV script; CSV values arrive as strings, so everything is cast."""
    user_key = 'CASE WHEN row.user_id = "deleted" THEN row.user_id ELSE ToInteger(row.user_id) END'
    return f"""\
// Synthetic StackOverflow-shaped dataset -> Memgraph.
// Generated by scripts/generate_dummy_data.py.
//
// The CSVs must be readable by the server process. With Docker:
//   docker cp csv memgraph-mage:/csv
//   docker exec -i memgraph-mage mgconsole < load_memgraph.cypher
//
// Indexes come first (every relationship below is a key lookup), and the
// uniqueness constraints go last because analytical mode rejects them.

STORAGE MODE IN_MEMORY_ANALYTICAL;

CREATE INDEX ON :User(uuid);
CREATE INDEX ON :Tag(name);
CREATE INDEX ON :Question(uuid);
CREATE INDEX ON :Answer(uuid);
CREATE INDEX ON :Comment(uuid);

LOAD CSV FROM "/csv/users.csv" WITH HEADER AS row
CREATE (:User {{
  uuid: CASE WHEN row.uuid = "deleted" THEN row.uuid ELSE ToInteger(row.uuid) END,
  display_name: row.display_name }});

LOAD CSV FROM "/csv/tags.csv" WITH HEADER AS row
CREATE (:Tag {{name: row.name, link: row.link}});

LOAD CSV FROM "/csv/questions.csv" WITH HEADER AS row
MATCH (u:User {{uuid: {user_key}}})
CREATE (u)-[:ASKED]->(:Question {{
  uuid: ToInteger(row.uuid),
  title: row.title,
  body_markdown: row.body_markdown,
  link: row.link,
  creation_date: ToInteger(row.creation_date),
  view_count: ToInteger(row.view_count),
  answer_count: ToInteger(row.answer_count),
  accepted_answer_id: CASE WHEN row.accepted_answer_id = ""
                           THEN Null ELSE ToInteger(row.accepted_answer_id) END }});

LOAD CSV FROM "/csv/answers.csv" WITH HEADER AS row
MATCH (u:User {{uuid: {user_key}}}), (q:Question {{uuid: ToInteger(row.question_id)}})
CREATE (a:Answer {{
  uuid: ToInteger(row.uuid),
  title: row.title,
  body_markdown: row.body_markdown,
  link: row.link,
  score: ToInteger(row.score),
  is_accepted: row.is_accepted = "1" }}),
  (u)-[:PROVIDED]->(a), (a)-[:ANSWERED]->(q);

LOAD CSV FROM "/csv/comments.csv" WITH HEADER AS row
MATCH (u:User {{uuid: {user_key}}}), (q:Question {{uuid: ToInteger(row.question_id)}})
CREATE (c:Comment {{
  uuid: ToInteger(row.uuid),
  link: row.link,
  score: ToInteger(row.score) }}),
  (u)-[:COMMENTED]->(c), (c)-[:COMMENTED_ON]->(q);

LOAD CSV FROM "/csv/question_tags.csv" WITH HEADER AS row
MATCH (q:Question {{uuid: ToInteger(row.question_id)}}), (t:Tag {{name: row.tag_name}})
CREATE (q)-[:TAGGED]->(t);

STORAGE MODE IN_MEMORY_TRANSACTIONAL;

CREATE CONSTRAINT ON (n:User) ASSERT n.uuid IS UNIQUE;
CREATE CONSTRAINT ON (n:Tag) ASSERT n.name IS UNIQUE;
CREATE CONSTRAINT ON (n:Question) ASSERT n.uuid IS UNIQUE;
CREATE CONSTRAINT ON (n:Answer) ASSERT n.uuid IS UNIQUE;
CREATE CONSTRAINT ON (n:Comment) ASSERT n.uuid IS UNIQUE;
"""


if __name__ == "__main__":
    main()
