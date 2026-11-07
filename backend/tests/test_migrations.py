"""Real Alembic migrations against an empty Postgres database (the other tests use create_all and
therefore never see migration bugs such as wrong enum labels or an enum type created twice)."""

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import Enum, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from app.core.config import Settings
from app.db.base import Base
from app.models.family import Family
from app.models.food_preference import FoodPreferenceKind, RecipeRating, UserFoodPreference
from app.models.recipe import Recipe
from app.models.user import User, UserRole

BACKEND = Path(__file__).resolve().parent.parent
BASE_REVISION = "a91c2d4e6f08"  # last revision before the food-preference / rating feature
FEATURE_TABLES = {"user_food_preference", "recipe_rating", "family_device"}
RECIPE_FEATURE_COLUMNS = {"family_id", "source_url", "source_name", "image_url"}
MIGRATION_DB = "migration_test"
TABLES_SQL = "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
ENUM_COUNT_SQL = "SELECT count(*) FROM pg_type WHERE typname = 'food_preference_kind'"


@pytest.fixture(scope="module")
def migrated_db_url(_postgres_url):
    """A separate empty database, upgraded to head by the real migrations."""
    admin = create_engine(_postgres_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{MIGRATION_DB}"'))
        conn.execute(text(f'CREATE DATABASE "{MIGRATION_DB}"'))
    url = make_url(_postgres_url).set(database=MIGRATION_DB).render_as_string(hide_password=False)

    # alembic/env.py reads the URL via get_settings() when it runs: point it at the empty database.
    patch = pytest.MonkeyPatch()
    patch.setattr("app.core.config.get_settings", lambda: Settings(database_url=url))
    try:
        command.upgrade(_alembic_config(), "head")
        yield url
    finally:
        patch.undo()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{MIGRATION_DB}" WITH (FORCE)'))
        admin.dispose()


def _alembic_config() -> Config:
    # No ini file: env.py would otherwise call fileConfig() and reconfigure/disable the test loggers.
    config = Config()
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    return config


def test_feature_data_roundtrips_through_migrated_schema(migrated_db_url):
    """Every enum label the model can store is accepted by the migrated database."""
    engine = create_engine(migrated_db_url)
    try:
        with Session(engine) as session:
            family = Family(name="Migrationsfamilie")
            session.add(family)
            session.flush()
            parent = User(family_id=family.id, role=UserRole.PARENT_ADMIN, display_name="Mama", email="m@example.ch")
            session.add(parent)
            session.flush()
            for kind in FoodPreferenceKind:
                session.add(
                    UserFoodPreference(
                        family_id=family.id, user_id=parent.id, ingredient_name=f"zutat-{kind.value}", kind=kind
                    )
                )
            recipe = Recipe(family_id=family.id, name="Suppe", instructions="kochen", source_url="https://x.example/r")
            session.add(recipe)
            session.flush()
            session.add(
                RecipeRating(
                    family_id=family.id, recipe_id=recipe.id, user_id=parent.id, stars=4, cooked_on=date.today()
                )
            )
            session.commit()

            stored = {p.kind for p in session.query(UserFoodPreference).filter_by(user_id=parent.id)}
            assert stored == set(FoodPreferenceKind)
            assert session.query(RecipeRating).filter_by(user_id=parent.id).one().stars == 4
    finally:
        engine.dispose()


def test_enum_labels_in_database_match_the_models(migrated_db_url):
    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            for table in Base.metadata.tables.values():
                for column in table.columns:
                    if not isinstance(column.type, Enum) or column.type.name not in {"food_preference_kind"}:
                        continue
                    labels = [
                        row[0]
                        for row in conn.execute(
                            text(
                                "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                                "WHERE t.typname = :name ORDER BY e.enumsortorder"
                            ),
                            {"name": column.type.name},
                        )
                    ]
                    assert labels == list(column.type.enums), column.type.name
            count = conn.execute(text("SELECT count(*) FROM pg_type WHERE typname = 'food_preference_kind'")).scalar()
            assert count == 1
    finally:
        engine.dispose()


def test_migrated_schema_has_no_drift_for_the_feature_tables(migrated_db_url):
    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts={"compare_type": True, "compare_server_default": False})
            diffs = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    def concerns_feature(diff) -> bool:
        items = diff if isinstance(diff, list) else [diff]
        for item in items:
            kind = item[0]
            if kind in {"add_table", "remove_table"}:
                name = item[1].name
            elif kind in {"add_column", "remove_column"}:
                name = item[2]
                if name == "recipe" and item[3].name not in RECIPE_FEATURE_COLUMNS:
                    continue
            elif kind in {"add_index", "remove_index", "add_constraint", "remove_constraint", "add_fk", "remove_fk"}:
                obj = item[1]
                name = getattr(getattr(obj, "table", None), "name", None)
            else:  # modify_type / modify_nullable ...: (kind, schema, table, column, ...)
                name = item[2]
                if name == "recipe" and item[3] not in RECIPE_FEATURE_COLUMNS:
                    continue
            if name in FEATURE_TABLES or name == "recipe":
                return True
        return False

    relevant = [d for d in diffs if concerns_feature(d)]
    described = [
        f"{d[0]} {getattr(d[1], 'name', d[1])} on {getattr(getattr(d[1], 'table', None), 'name', '-')}"
        for d in relevant
    ]
    assert not described, "; ".join(described)


def test_downgrade_to_base_and_upgrade_again(migrated_db_url):
    engine = create_engine(migrated_db_url)
    config = _alembic_config()
    try:
        command.downgrade(config, BASE_REVISION)
        with engine.connect() as conn:
            tables = set(conn.execute(text(TABLES_SQL)).scalars())
            assert not (FEATURE_TABLES & tables)
            assert conn.execute(text(ENUM_COUNT_SQL)).scalar() == 0
        command.upgrade(config, "head")
        with engine.connect() as conn:
            tables = set(conn.execute(text(TABLES_SQL)).scalars())
            assert FEATURE_TABLES <= tables
            assert conn.execute(text(ENUM_COUNT_SQL)).scalar() == 1
    finally:
        engine.dispose()


def test_unique_rating_constraint_exists_after_migration(migrated_db_url):
    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            names = set(
                conn.execute(
                    text("SELECT conname FROM pg_constraint WHERE conrelid = 'recipe_rating'::regclass")
                ).scalars()
            )
        assert "recipe_rating_recipe_user_day_unique" in names
    finally:
        engine.dispose()


PREVIOUS_REVISION = "c2d3e4f5a6b7"
SOURCE_URL_INDEX = "recipe_family_source_url_unique"


def test_unique_source_url_index_exists_and_rejects_duplicates(migrated_db_url):
    from sqlalchemy.exc import IntegrityError

    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"), {"n": SOURCE_URL_INDEX}
            ).scalar_one()
        assert "UNIQUE" in row and "WHERE (source_url IS NOT NULL)" in row
        with Session(engine) as session:
            family = Family(name="Indexfamilie")
            session.add(family)
            session.flush()
            session.add_all([Recipe(family_id=family.id, name=n, instructions="x") for n in ("A", "B")])  # NULL urls
            session.add(Recipe(family_id=family.id, name="C", instructions="x", source_url="https://x.example/1"))
            session.commit()
            session.add(Recipe(family_id=family.id, name="D", instructions="x", source_url="https://x.example/1"))
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        engine.dispose()


def test_upgrade_dedupes_existing_duplicate_source_urls(migrated_db_url):
    engine = create_engine(migrated_db_url)
    config = _alembic_config()
    try:
        command.downgrade(config, PREVIOUS_REVISION)
        with Session(engine) as session:
            family = Family(name="Duplikatfamilie")
            session.add(family)
            session.commit()
            for name, created in (("alt", "2026-01-01"), ("neu", "2026-02-01"), ("neuer", "2026-03-01")):
                session.execute(
                    text(
                        "INSERT INTO recipe (id, name, instructions, servings, prep_minutes, family_id, source_url,"
                        " created_at) VALUES (gen_random_uuid(), :n, 'x', 4, 30, :f, 'https://dup.example/r', :c)"
                    ),
                    {"n": name, "f": family.id, "c": created},
                )
            session.commit()
            family_id = family.id
        command.upgrade(config, "head")
        with engine.connect() as conn:
            rows = dict(
                conn.execute(text("SELECT name, source_url FROM recipe WHERE family_id = :f"), {"f": family_id}).all()
            )
        assert rows == {"alt": "https://dup.example/r", "neu": None, "neuer": None}  # nothing deleted
    finally:
        engine.dispose()


def test_family_device_table_has_unique_token_hash_index_and_cascade_fk(migrated_db_url):
    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            indexdef = conn.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_family_device_token_hash'")
            ).scalar()
            assert indexdef is not None and "UNIQUE" in indexdef.upper()
            cascade = conn.execute(
                text(
                    "SELECT confdeltype FROM pg_constraint WHERE conrelid = 'family_device'::regclass "
                    "AND confrelid = 'family'::regclass"
                )
            ).scalar()
            assert cascade == "c"  # ON DELETE CASCADE
    finally:
        engine.dispose()


def test_family_device_migration_downgrades_and_upgrades_cleanly(migrated_db_url):
    engine = create_engine(migrated_db_url)
    config = _alembic_config()
    try:
        command.downgrade(config, "d3e4f5a6b7c8")
        with engine.connect() as conn:
            assert "family_device" not in set(conn.execute(text(TABLES_SQL)).scalars())
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert "family_device" in set(conn.execute(text(TABLES_SQL)).scalars())
    finally:
        engine.dispose()


LOCKOUT_REVISION = "f5a6b7c8d9e0"
USER_LOCK_COLUMNS = {"failed_pin_attempts", "pin_locked_until", "pin_lock_level"}
DEVICE_LOCK_COLUMNS = {"failed_pin_attempts", "pin_window_started_at"}
COLUMNS_SQL = "SELECT column_name FROM information_schema.columns WHERE table_name = :t"


def _columns(conn, table):
    return set(conn.execute(text(COLUMNS_SQL), {"t": table}).scalars())


def test_pin_lockout_columns_exist_with_defaults_and_no_drift(migrated_db_url):
    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            assert USER_LOCK_COLUMNS <= _columns(conn, "user")
            assert DEVICE_LOCK_COLUMNS <= _columns(conn, "family_device")
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diffs = compare_metadata(context, Base.metadata)
        lockout = [
            d for d in diffs
            if not isinstance(d, list) and d[0] in {"add_column", "remove_column"}
            and d[2] in {"user", "family_device"} and d[3].name in USER_LOCK_COLUMNS | DEVICE_LOCK_COLUMNS
        ]
        assert not lockout
    finally:
        engine.dispose()


def test_pin_lockout_migration_defaults_existing_rows_and_roundtrips(migrated_db_url):
    engine = create_engine(migrated_db_url)
    config = _alembic_config()
    try:
        command.downgrade(config, "e4f5a6b7c8d9")
        with engine.connect() as conn:
            assert not (USER_LOCK_COLUMNS & _columns(conn, "user"))
            assert not (DEVICE_LOCK_COLUMNS & _columns(conn, "family_device"))
            family_id = conn.execute(
                text("INSERT INTO family (id, name, hosting_mode, locale, created_at) "
                     "VALUES (gen_random_uuid(), 'Alt', 'cloud_ch', 'de-CH', now()) RETURNING id")
            ).scalar_one()
            conn.execute(
                text("INSERT INTO \"user\" (id, family_id, role, display_name, created_at) "
                     "VALUES (gen_random_uuid(), :f, 'CHILD', 'Altkind', now())"),
                {"f": family_id},
            )
            conn.commit()
        command.upgrade(config, "head")
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT failed_pin_attempts, pin_locked_until, pin_lock_level FROM \"user\" "
                     "WHERE display_name = 'Altkind'")
            ).one()
            assert tuple(row) == (0, None, 0)
    finally:
        engine.dispose()


ARCHIVE_REVISION = "g6b7c8d9e0f1"


def test_task_template_archived_at_column_exists_and_has_no_drift(migrated_db_url):
    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            info = conn.execute(
                text(
                    "SELECT data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'task_template' AND column_name = 'archived_at'"
                )
            ).one()
            assert tuple(info) == ("timestamp with time zone", "YES")
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diffs = compare_metadata(context, Base.metadata)
        archive = [
            d for d in diffs
            if not isinstance(d, list) and d[0] in {"add_column", "remove_column"}
            and d[2] == "task_template" and d[3].name == "archived_at"
        ]
        assert not archive
    finally:
        engine.dispose()


def test_task_template_archived_at_migration_roundtrips_and_keeps_rows(migrated_db_url):
    engine = create_engine(migrated_db_url)
    config = _alembic_config()
    try:
        command.downgrade(config, LOCKOUT_REVISION)
        with engine.connect() as conn:
            assert "archived_at" not in _columns(conn, "task_template")
            family_id = conn.execute(
                text("INSERT INTO family (id, name, hosting_mode, locale, created_at) "
                     "VALUES (gen_random_uuid(), 'Vorlage', 'cloud_ch', 'de-CH', now()) RETURNING id")
            ).scalar_one()
            conn.execute(
                text("INSERT INTO task_template (id, family_id, title, kind, points_value, bonus_multiplier, "
                     "money_value_chf, assignee_role, created_at) VALUES (gen_random_uuid(), :f, 'Alt', 'BASIS', "
                     "0, 1.0, 0.0, 'any', now())"),
                {"f": family_id},
            )
            conn.commit()
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert "archived_at" in _columns(conn, "task_template")
            archived = conn.execute(text("SELECT archived_at FROM task_template WHERE title = 'Alt'")).scalar_one()
            assert archived is None
            # The archive revision is no longer the head once later revisions exist: it must be part of the
            # migrated history (the head itself is checked by the later revisions' own tests).
            head = ScriptDirectory.from_config(config).get_current_head()
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == head
            assert ARCHIVE_REVISION in {r.revision for r in ScriptDirectory.from_config(config).walk_revisions()}
    finally:
        engine.dispose()


GOOGLE_REVISION = "h7c8d9e0f1a2"
GOOGLE_ACCOUNT_COLUMNS = {"google_email", "status", "last_error"}
GOOGLE_EVENT_COLUMNS = {"sync_account_id", "source_calendar_id", "is_private"}
GOOGLE_TABLES = {"calendar_sync_account", "calendar_event", "google_calendar_selection"}
INDEX_LIKE = {"add_index", "remove_index", "add_constraint", "remove_constraint", "add_fk", "remove_fk"}


def test_google_sync_schema_exists_and_has_no_drift(migrated_db_url):
    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            assert GOOGLE_ACCOUNT_COLUMNS <= _columns(conn, "calendar_sync_account")
            assert GOOGLE_EVENT_COLUMNS <= _columns(conn, "calendar_event")
            assert {"id", "account_id", "google_calendar_id", "name", "owner_user_ids"} <= _columns(
                conn, "google_calendar_selection"
            )
            # ON DELETE SET NULL on the event provenance, ON DELETE CASCADE on the selections.
            assert conn.execute(
                text(
                    "SELECT confdeltype FROM pg_constraint WHERE conrelid = 'calendar_event'::regclass "
                    "AND confrelid = 'calendar_sync_account'::regclass"
                )
            ).scalar_one() == "n"
            assert conn.execute(
                text(
                    "SELECT confdeltype FROM pg_constraint WHERE conrelid = 'google_calendar_selection'::regclass "
                    "AND confrelid = 'calendar_sync_account'::regclass"
                )
            ).scalar_one() == "c"
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diffs = compare_metadata(context, Base.metadata)

        def concerns_google(diff) -> bool:
            items = diff if isinstance(diff, list) else [diff]
            for item in items:
                kind = item[0]
                if kind in {"add_table", "remove_table"}:
                    name = item[1].name
                elif kind in {"add_column", "remove_column"}:
                    name = item[2]
                elif kind in INDEX_LIKE:
                    name = getattr(getattr(item[1], "table", None), "name", None)
                else:
                    name = item[2]
                if name in GOOGLE_TABLES:
                    return True
            return False

        relevant = [d for d in diffs if concerns_google(d)]
        assert not relevant, str(relevant)
    finally:
        engine.dispose()


def test_google_sync_migration_roundtrips_and_preselects_primary_calendar(migrated_db_url):
    engine = create_engine(migrated_db_url)
    config = _alembic_config()
    try:
        command.downgrade(config, ARCHIVE_REVISION)
        with engine.connect() as conn:
            assert not (GOOGLE_ACCOUNT_COLUMNS & _columns(conn, "calendar_sync_account"))
            assert not (GOOGLE_EVENT_COLUMNS & _columns(conn, "calendar_event"))
            assert "google_calendar_selection" not in set(conn.execute(text(TABLES_SQL)).scalars())
            family_id = conn.execute(
                text("INSERT INTO family (id, name, hosting_mode, locale, created_at) "
                     "VALUES (gen_random_uuid(), 'Google', 'cloud_ch', 'de-CH', now()) RETURNING id")
            ).scalar_one()
            user_id = conn.execute(
                text("INSERT INTO \"user\" (id, family_id, role, display_name, created_at) "
                     "VALUES (gen_random_uuid(), :f, 'PARENT_ADMIN', 'Mama', now()) RETURNING id"),
                {"f": family_id},
            ).scalar_one()
            conn.execute(
                text("INSERT INTO calendar_sync_account (id, user_id, provider, created_at) "
                     "VALUES (gen_random_uuid(), :u, 'google', now())"),
                {"u": user_id},
            )
            for source in ("google", "native"):
                conn.execute(
                    text("INSERT INTO calendar_event (id, family_id, title, start, \"end\", all_day, source, "
                         "created_at) VALUES (gen_random_uuid(), :f, :t, now(), now(), false, :s, now())"),
                    {"f": family_id, "t": f"alt-{source}", "s": source},
                )
            conn.commit()
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert GOOGLE_ACCOUNT_COLUMNS <= _columns(conn, "calendar_sync_account")
            assert GOOGLE_EVENT_COLUMNS <= _columns(conn, "calendar_event")
            status, error = conn.execute(text("SELECT status, last_error FROM calendar_sync_account")).one()
            assert (status, error) == ("ok", None)
            # Orphaned ownerless google rows are dropped, everything else is kept.
            titles = set(
                conn.execute(text("SELECT title FROM calendar_event WHERE family_id = :f"), {"f": family_id}).scalars()
            )
            assert titles == {"alt-native"}
            private = conn.execute(text("SELECT is_private FROM calendar_event")).scalar_one()
            assert private is False
            # The existing google account keeps importing its primary calendar for the whole family.
            row = conn.execute(
                text("SELECT google_calendar_id, owner_user_ids FROM google_calendar_selection")
            ).one()
            assert row[0] == "primary" and list(row[1]) == []
            head = ScriptDirectory.from_config(config).get_current_head()
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == head
    finally:
        engine.dispose()


SCHOOL_BREAK_REVISION = "i8d9e0f1a2b3"
SCHOOL_BREAK_COLUMNS = {
    "id", "family_id", "title", "kind", "start_date", "end_date", "child_user_ids", "source",
    "sync_account_id", "source_calendar_id", "external_id", "created_at",
}


def test_school_break_table_exists_has_constraints_and_no_drift(migrated_db_url):
    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            assert SCHOOL_BREAK_COLUMNS <= _columns(conn, "school_break")
            for referenced in ("family", "calendar_sync_account"):
                assert conn.execute(
                    text(
                        "SELECT confdeltype FROM pg_constraint WHERE conrelid = 'school_break'::regclass "
                        "AND confrelid = CAST(:t AS regclass)"
                    ),
                    {"t": referenced},
                ).scalar_one() == "c"  # ON DELETE CASCADE
            indexdef = conn.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_school_break_google_event'")
            ).scalar_one()
            assert "UNIQUE" in indexdef and "sync_account_id, source_calendar_id, external_id" in indexdef
            checks = set(
                conn.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'school_break'::regclass AND contype = 'c'"
                    )
                ).scalars()
            )
            assert "ck_school_break_dates" in checks
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diffs = compare_metadata(context, Base.metadata)

        def concerns_school_break(diff) -> bool:
            for item in diff if isinstance(diff, list) else [diff]:
                if item[0] in {"add_table", "remove_table"}:
                    name = item[1].name
                elif item[0] in {"add_column", "remove_column"}:
                    name = item[2]
                elif item[0] in INDEX_LIKE:
                    name = getattr(getattr(item[1], "table", None), "name", None)
                else:
                    name = item[2]
                if name == "school_break":
                    return True
            return False

        relevant = [d for d in diffs if concerns_school_break(d)]
        assert not relevant, str(relevant)
    finally:
        engine.dispose()


def test_school_break_migration_roundtrips_and_the_constraints_hold(migrated_db_url):
    from sqlalchemy.exc import IntegrityError

    engine = create_engine(migrated_db_url)
    config = _alembic_config()
    try:
        command.downgrade(config, GOOGLE_REVISION)
        with engine.connect() as conn:
            assert "school_break" not in set(conn.execute(text(TABLES_SQL)).scalars())
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert "school_break" in set(conn.execute(text(TABLES_SQL)).scalars())
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                ScriptDirectory.from_config(config).get_current_head()
            )
            assert SCHOOL_BREAK_REVISION in {r.revision for r in ScriptDirectory.from_config(config).walk_revisions()}

        with Session(engine) as session:
            family = Family(name="Ferienfamilie")
            session.add(family)
            session.flush()
            insert = text(
                "INSERT INTO school_break (id, family_id, title, start_date, end_date, created_at) "
                "VALUES (gen_random_uuid(), :f, 'Ferien', :s, :e, now())"
            )
            session.execute(insert, {"f": family.id, "s": date(2026, 10, 12), "e": date(2026, 10, 23)})
            kind, source, children = session.execute(
                text("SELECT kind, source, child_user_ids FROM school_break WHERE family_id = :f"), {"f": family.id}
            ).one()
            assert (kind, source, list(children)) == ("ferien", "manual", [])  # server defaults
            session.commit()
            with pytest.raises(IntegrityError):  # end before start
                session.execute(insert, {"f": family.id, "s": date(2026, 10, 23), "e": date(2026, 10, 12)})
            session.rollback()
            # Deleting the family removes its breaks (ON DELETE CASCADE).
            session.execute(text("DELETE FROM family WHERE id = :f"), {"f": family.id})
            session.commit()
            assert session.execute(text("SELECT count(*) FROM school_break")).scalar_one() == 0
    finally:
        engine.dispose()


DAY_PHASE_REVISION = "j9e0f1a2b3c4"
DAY_PHASE_COLUMNS = {
    "id", "family_id", "name", "icon", "start_time", "end_time", "day_type", "sort_order", "created_at",
}


def test_day_phase_tables_columns_constraints_and_no_drift(migrated_db_url):
    engine = create_engine(migrated_db_url)
    try:
        with engine.connect() as conn:
            assert DAY_PHASE_COLUMNS <= _columns(conn, "day_phase")
            assert {"template_id", "phase_id"} <= _columns(conn, "task_template_phase")
            assert "carry_over" in _columns(conn, "task_template")
            assert "phase_id" in _columns(conn, "task_instance")

            def delete_rule(table: str, referenced: str) -> str:
                return conn.execute(
                    text(
                        "SELECT confdeltype FROM pg_constraint WHERE conrelid = CAST(:t AS regclass) "
                        "AND confrelid = CAST(:r AS regclass)"
                    ),
                    {"t": table, "r": referenced},
                ).scalar_one()

            assert delete_rule("day_phase", "family") == "c"
            assert delete_rule("task_template_phase", "task_template") == "c"
            assert delete_rule("task_template_phase", "day_phase") == "c"
            assert delete_rule("task_instance", "day_phase") == "n"  # ON DELETE SET NULL
            checks = set(
                conn.execute(
                    text("SELECT conname FROM pg_constraint WHERE conrelid = 'day_phase'::regclass AND contype = 'c'")
                ).scalars()
            )
            assert {"ck_day_phase_times", "ck_day_phase_day_type"} <= checks
            labels = set(
                conn.execute(
                    text(
                        "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                        "WHERE t.typname = 'task_status'"
                    )
                ).scalars()
            )
            assert {"OPEN", "COMPLETED", "APPROVED", "EXPIRED"} == labels
            context = MigrationContext.configure(conn, opts={"compare_type": True})
            diffs = compare_metadata(context, Base.metadata)
        words = ("day_phase", "task_template_phase", "carry_over", "phase_id")
        relevant = [d for d in diffs if any(word in str(d) for word in words)]
        assert not relevant, str(relevant)
    finally:
        engine.dispose()


def test_day_phase_migration_roundtrips_and_the_constraints_hold(migrated_db_url):
    from sqlalchemy.exc import IntegrityError

    engine = create_engine(migrated_db_url)
    config = _alembic_config()
    try:
        command.downgrade(config, SCHOOL_BREAK_REVISION)
        with engine.connect() as conn:
            tables = set(conn.execute(text(TABLES_SQL)).scalars())
            assert "day_phase" not in tables and "task_template_phase" not in tables
            assert "carry_over" not in _columns(conn, "task_template")
            assert "phase_id" not in _columns(conn, "task_instance")
        command.upgrade(config, "head")
        command.downgrade(config, SCHOOL_BREAK_REVISION)  # the kept enum label does not break a second cycle
        command.upgrade(config, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
                ScriptDirectory.from_config(config).get_current_head()
            )

        with Session(engine) as session:
            family = Family(name="Phasenfamilie")
            session.add(family)
            session.flush()
            insert = text(
                "INSERT INTO day_phase (id, family_id, name, start_time, end_time, day_type, created_at) "
                "VALUES (gen_random_uuid(), :f, 'Mittag', :s, :e, :t, now())"
            )
            ok = {"f": family.id, "s": "11:30", "e": "13:30", "t": "both"}
            session.execute(insert, ok)
            sort_order = session.execute(text("SELECT sort_order FROM day_phase")).scalar_one()
            assert sort_order == 0  # server default
            session.commit()
            for bad in ({**ok, "e": "11:30"}, {**ok, "e": "10:00"}, {**ok, "t": "weekend"}):
                with pytest.raises(IntegrityError):
                    session.execute(insert, bad)
                session.rollback()
            # Deleting the family removes its phases (ON DELETE CASCADE).
            session.execute(text("DELETE FROM family WHERE id = :f"), {"f": family.id})
            session.commit()
            assert session.execute(text("SELECT count(*) FROM day_phase")).scalar_one() == 0
    finally:
        engine.dispose()
