"""
file: tables.py
importable: Base, RepoRow, PullRequestRow, InteractionRow, CommitRow

The SQLAlchemy classes behind the database tables. One object of a Row class
is one row of its table. They mirror the classes in domain/models.py but stay
apart from them, stores.py converts between the two so nothing outside
storage ever sees a Row.

An Actor is not a table of its own, it is two columns wherever it is used, a
login and an is_bot. is_bot is decided differently for pull requests and for
commits, so one shared accounts table could not hold a single right value.

description: Database tables for repos and their raw pull request and commit data.
"""

# NOTE: Deleting a repo's pull requests also deletes their interactions through
# ON DELETE CASCADE. SQLite only honours that when foreign_keys is switched on
# for the connection, database.py does it for every connection it opens.

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

# --- Types ---

# SQLite keeps no time zone and hands datetimes back naive, while models.py
# wants every datetime aware and in UTC. Values are turned into UTC on the way
# in and marked as UTC on the way out. A naive value is refused instead of
# guessed, astimezone would quietly read it as the computer's local time.
class UtcDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"Datetime {value} has no time zone, only aware datetimes can be stored")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)

# --- Tables ---

# Every Row class inherits from this, create_tables finds the tables through it.
class Base(DeclarativeBase):
    pass

# id is GitHub's own numeric repo id and not one made up here, so a renamed or
# moved repo keeps its row and only owner and name change. The enrichment
# columns stay empty until the repo is fetched for the first time. The
# requested_since columns hold where each fetch was asked to start, the
# complete_since ones where its data really starts.
class RepoRow(Base):
    __tablename__ = "repos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    owner: Mapped[str]
    name: Mapped[str]
    enriched_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    prs_complete_since: Mapped[datetime | None] = mapped_column(UtcDateTime)
    commits_complete_since: Mapped[datetime | None] = mapped_column(UtcDateTime)
    prs_requested_since: Mapped[datetime | None] = mapped_column(UtcDateTime)
    commits_requested_since: Mapped[datetime | None] = mapped_column(UtcDateTime)

# Rows are read back ordered by id, which keeps the order they were saved in,
# newest first like GitHub sent them. closed_via holds a CloseSource value.
# labels is a JSON list of label names. Nothing searches by label in SQL, the
# metrics filter them in Python, so a table of its own would only add a join.
class PullRequestRow(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (UniqueConstraint("repo_id", "number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"))
    number: Mapped[int]
    author_login: Mapped[str]
    author_is_bot: Mapped[bool]
    author_association: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    merged_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    closed_by_login: Mapped[str | None]
    closed_by_is_bot: Mapped[bool | None]
    closed_via: Mapped[str | None]
    labels: Mapped[list[str]] = mapped_column(JSON)

    # passive_deletes leaves removing interactions to the database cascade, so
    # deleting pull requests never has to load their interactions first.
    interactions: Mapped[list["InteractionRow"]] = relationship(order_by="InteractionRow.id", passive_deletes=True)

# One comment or one review of a pull request, kept in the order GitHub sent
# them, comments first and reviews after.
class InteractionRow(Base):
    __tablename__ = "interactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    pull_request_id: Mapped[int] = mapped_column(ForeignKey("pull_requests.id", ondelete="CASCADE"), index=True)
    actor_login: Mapped[str]
    actor_is_bot: Mapped[bool]
    association: Mapped[str]
    at: Mapped[datetime] = mapped_column(UtcDateTime)

# Merge commits never get here, client.py drops them while fetching. Bot
# commits do and are only marked.
class CommitRow(Base):
    __tablename__ = "commits"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"), index=True)
    author_login: Mapped[str]
    author_is_bot: Mapped[bool]
    committed_at: Mapped[datetime] = mapped_column(UtcDateTime)
