#!/usr/bin/env python3
"""Export the stackoverflow50 Neo4j graph as a loadable MySQL dump.

Reads the graph over Neo4j's HTTP Query API and writes CREATE TABLE + INSERT
statements, which MySQL loads with:

    mysql -u root -p < stackoverflow50_mysql.sql

The graph is strictly hierarchical -- every Question has exactly one asker,
every Answer one author and one question, every Comment one author and one
question -- so each relationship becomes a NOT NULL foreign key rather than a
junction table. Only Question-TAGGED->Tag is many-to-many.

    (:User)-[:ASKED]->(:Question)        questions.user_id
    (:User)-[:PROVIDED]->(:Answer)       answers.user_id
    (:Answer)-[:ANSWERED]->(:Question)   answers.question_id
    (:User)-[:COMMENTED]->(:Comment)     comments.user_id
    (:Comment)-[:COMMENTED_ON]->(:Q)     comments.question_id
    (:Question)-[:TAGGED]->(:Tag)        question_tags

creation_date keeps Neo4j's epoch-seconds integer; use FROM_UNIXTIME() to read
it as a date. Node keys (uuid, Tag.name) become primary keys, so the uniqueness
constraints carry over from the graph. User.uuid is VARCHAR, not an integer:
one node in the graph is the deleted account with uuid "deleted", and widening
the key keeps the export lossless.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from neo4j_to_cypherl import Neo4j  # noqa: E402  (shared HTTP Query API client)

MAX_STATEMENT_BYTES = 800_000

SCHEMA = """\
DROP TABLE IF EXISTS `question_tags`;
DROP TABLE IF EXISTS `comments`;
DROP TABLE IF EXISTS `answers`;
DROP TABLE IF EXISTS `questions`;
DROP TABLE IF EXISTS `tags`;
DROP TABLE IF EXISTS `users`;

CREATE TABLE `users` (
  `uuid` VARCHAR(32) NOT NULL COMMENT 'numeric StackOverflow id, or the literal "deleted"',
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
  `user_id` VARCHAR(32) NOT NULL COMMENT 'asker: (:User)-[:ASKED]->(:Question)',
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
  `question_id` BIGINT UNSIGNED NOT NULL COMMENT '(:Answer)-[:ANSWERED]->(:Question)',
  `user_id` VARCHAR(32) NOT NULL COMMENT '(:User)-[:PROVIDED]->(:Answer)',
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
  `question_id` BIGINT UNSIGNED NOT NULL COMMENT '(:Comment)-[:COMMENTED_ON]->(:Question)',
  `user_id` VARCHAR(32) NOT NULL COMMENT '(:User)-[:COMMENTED]->(:Comment)',
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

-- questions.accepted_answer_id is added after `answers` exists (circular reference).
ALTER TABLE `questions`
  ADD CONSTRAINT `fk_questions_accepted_answer` FOREIGN KEY (`accepted_answer_id`)
  REFERENCES `answers` (`uuid`) ON DELETE SET NULL;
"""

# (table, columns, Cypher source). Each query carries SKIP $skip LIMIT $limit.
TABLES = [
    (
        "users",
        ["uuid", "display_name"],
        "MATCH (u:User) RETURN u.uuid AS uuid, u.display_name AS display_name "
        "ORDER BY uuid SKIP $skip LIMIT $limit",
    ),
    (
        "tags",
        ["name", "link"],
        "MATCH (t:Tag) RETURN t.name AS name, t.link AS link "
        "ORDER BY name SKIP $skip LIMIT $limit",
    ),
    (
        "questions",
        [
            "uuid",
            "title",
            "body_markdown",
            "link",
            "creation_date",
            "view_count",
            "answer_count",
            "accepted_answer_id",
            "user_id",
        ],
        "MATCH (u:User)-[:ASKED]->(q:Question) "
        "RETURN q.uuid AS uuid, q.title AS title, q.body_markdown AS body_markdown, "
        "q.link AS link, q.creation_date AS creation_date, q.view_count AS view_count, "
        "q.answer_count AS answer_count, q.accepted_answer_id AS accepted_answer_id, "
        "u.uuid AS user_id ORDER BY uuid SKIP $skip LIMIT $limit",
    ),
    (
        "answers",
        [
            "uuid",
            "title",
            "body_markdown",
            "link",
            "score",
            "is_accepted",
            "question_id",
            "user_id",
        ],
        "MATCH (u:User)-[:PROVIDED]->(a:Answer)-[:ANSWERED]->(q:Question) "
        "RETURN a.uuid AS uuid, a.title AS title, a.body_markdown AS body_markdown, "
        "a.link AS link, a.score AS score, a.is_accepted AS is_accepted, "
        "q.uuid AS question_id, u.uuid AS user_id ORDER BY uuid SKIP $skip LIMIT $limit",
    ),
    (
        "comments",
        ["uuid", "link", "score", "question_id", "user_id"],
        "MATCH (u:User)-[:COMMENTED]->(c:Comment)-[:COMMENTED_ON]->(q:Question) "
        "RETURN c.uuid AS uuid, c.link AS link, c.score AS score, "
        "q.uuid AS question_id, u.uuid AS user_id ORDER BY uuid SKIP $skip LIMIT $limit",
    ),
    (
        "question_tags",
        ["question_id", "tag_name"],
        "MATCH (q:Question)-[:TAGGED]->(t:Tag) "
        "RETURN q.uuid AS question_id, t.name AS tag_name "
        "ORDER BY question_id, tag_name SKIP $skip LIMIT $limit",
    ),
]

ESCAPES = {
    "\\": "\\\\",
    "'": "\\'",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\x00": "\\0",
    "\x1a": "\\Z",
}


def literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'" + "".join(ESCAPES.get(ch, ch) for ch in value) + "'"
    sys.exit(f"Unsupported value type {type(value).__name__}: {value!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:7474")
    parser.add_argument("--database", default="stackoverflow50")
    parser.add_argument("--user", default="neo4j")
    parser.add_argument("--password", default="password")
    parser.add_argument("--schema", default="stackoverflow50", help="MySQL schema name")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    db = Neo4j(args.url, args.database, args.user, args.password)
    counts = {}

    with open(args.out, "w", encoding="utf-8") as out:
        out.write(
            f"-- MySQL dump of the `{args.database}` Neo4j graph, "
            "generated by scripts/stackoverflow_to_mysql.py\n"
            f"-- Load with: mysql -u root -p < {Path(args.out).name}\n\n"
            "SET NAMES utf8mb4;\n"
            "SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS;\n"
            "SET FOREIGN_KEY_CHECKS=0;\n"
            "SET @OLD_SQL_MODE=@@SQL_MODE;\n"
            "SET SQL_MODE='STRICT_ALL_TABLES';\n\n"
            f"CREATE DATABASE IF NOT EXISTS `{args.schema}` "
            "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;\n"
            f"USE `{args.schema}`;\n\n"
        )
        out.write(SCHEMA)

        for table, columns, statement in TABLES:
            out.write(f"\n--\n-- Data for `{table}`\n--\n\n")
            prefix = f"INSERT INTO `{table}` (" + ", ".join(f"`{c}`" for c in columns) + ") VALUES\n"
            rows = 0
            batch = []
            size = 0

            def flush():
                nonlocal batch, size
                if batch:
                    out.write(prefix + ",\n".join(batch) + ";\n")
                    batch, size = [], 0

            for row in db.paginate(statement):
                tuple_sql = "(" + ", ".join(literal(row[c]) for c in columns) + ")"
                if size + len(tuple_sql) > MAX_STATEMENT_BYTES:
                    flush()
                batch.append(tuple_sql)
                size += len(tuple_sql) + 2
                rows += 1
            flush()
            counts[table] = rows

        out.write(
            "\nSET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS;\n"
            "SET SQL_MODE=@OLD_SQL_MODE;\n"
        )

    print(f"wrote {args.out}")
    for table, rows in counts.items():
        print(f"  {table:<14} {rows:>6} rows")


if __name__ == "__main__":
    main()
