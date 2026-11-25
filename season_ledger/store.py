from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

from .domain import Season, age_on, apply_competition_scope, current_season
from .ratings import apply_position_adjustment
from .points import calculate_season_points


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS seasons (
                    key TEXT PRIMARY KEY, start_year INTEGER NOT NULL, calendar INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS players (
                    id INTEGER PRIMARY KEY, season TEXT NOT NULL REFERENCES seasons(key),
                    transfermarkt_id TEXT NOT NULL, snapshot TEXT NOT NULL,
                    tm_json TEXT NOT NULL, fm_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                    last_error TEXT, UNIQUE(season, transfermarkt_id)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, season TEXT NOT NULL REFERENCES seasons(key),
                    transfermarkt_id TEXT NOT NULL, fotmob_id TEXT, player_id INTEGER,
                    status TEXT NOT NULL, message TEXT NOT NULL, error TEXT,
                    player_name TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS active_job ON jobs(season, transfermarkt_id)
                    WHERE status IN ('queued', 'running');
            """)
        self.add_season(current_season())

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_season(self, season: Season) -> dict:
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO seasons(key,start_year,calendar) VALUES (?,?,?)",
                       (season.key, season.start_year, int(season.calendar)))
        return {**season.as_dict(), "player_count": len(self.players(season.key))}

    def seasons(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT s.*, COUNT(p.id) player_count FROM seasons s LEFT JOIN players p ON p.season=s.key GROUP BY s.key ORDER BY s.start_year DESC,s.calendar").fetchall()
        return [{**Season(row["start_year"], bool(row["calendar"])).as_dict(), "player_count": row["player_count"]} for row in rows]

    def has_season(self, key: str) -> bool:
        with self.connection() as db:
            return db.execute("SELECT 1 FROM seasons WHERE key=?", (key,)).fetchone() is not None

    @staticmethod
    def _player(row) -> dict | None:
        if row is None:
            return None
        snapshot = apply_position_adjustment(apply_competition_scope(json.loads(row["snapshot"])))
        snapshot["season_points"] = calculate_season_points(snapshot)
        age_as_of = Season.parse(row["season"]).cutoff
        return {**snapshot, "id": row["id"], "season": row["season"],
                "age": age_on(snapshot.get("birth_date"), age_as_of), "age_as_of": age_as_of.isoformat(),
                "updated_at": row["updated_at"], "error": row["last_error"]}

    def players(self, season: str) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM players WHERE season=? ORDER BY id", (season,)).fetchall()
        return [self._player(row) for row in rows]

    def player(self, identity: int) -> dict | None:
        with self.connection() as db:
            return self._player(db.execute("SELECT * FROM players WHERE id=?", (identity,)).fetchone())

    def find_player(self, season: str, tm_id: str) -> dict | None:
        with self.connection() as db:
            return self._player(db.execute("SELECT * FROM players WHERE season=? AND transfermarkt_id=?", (season, tm_id)).fetchone())

    def raw(self, identity: int) -> tuple[dict, dict]:
        with self.connection() as db:
            row = db.execute("SELECT tm_json,fm_json FROM players WHERE id=?", (identity,)).fetchone()
        return (json.loads(row[0]), json.loads(row[1])) if row else ({}, {})

    def save_player(self, season: str, tm_id: str, snapshot: dict, tm: dict, fm: dict) -> int:
        with self.connection() as db:
            db.execute("""INSERT INTO players(season,transfermarkt_id,snapshot,tm_json,fm_json,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(season,transfermarkt_id) DO UPDATE SET
                snapshot=excluded.snapshot, tm_json=excluded.tm_json, fm_json=excluded.fm_json,
                updated_at=excluded.updated_at, last_error=NULL""",
                (season, tm_id, json.dumps(snapshot, ensure_ascii=False), json.dumps(tm, ensure_ascii=False), json.dumps(fm, ensure_ascii=False), now()))
            return db.execute("SELECT id FROM players WHERE season=? AND transfermarkt_id=?", (season, tm_id)).fetchone()[0]

    def delete_player(self, identity: int) -> bool:
        with self.connection() as db:
            row = db.execute("SELECT season,transfermarkt_id FROM players WHERE id=?", (identity,)).fetchone()
            if row is None:
                return False
            # Deleting a row while its refresh is active would resurrect it later.
            if db.execute("SELECT 1 FROM jobs WHERE season=? AND transfermarkt_id=? AND status IN ('queued','running')", tuple(row)).fetchone():
                raise ValueError("Дождитесь завершения обновления перед удалением игрока.")
            db.execute("DELETE FROM players WHERE id=?", (identity,))
            return True

    def fail_player(self, identity: int | None, message: str):
        if identity:
            with self.connection() as db:
                db.execute("UPDATE players SET last_error=? WHERE id=?", (message, identity))

    def create_job(self, season: str, tm_id: str, fm_id: str | None = None, player_id: int | None = None) -> dict:
        with self.connection() as db:
            pending = db.execute("SELECT * FROM jobs WHERE season=? AND transfermarkt_id=? AND status IN ('queued','running')", (season, tm_id)).fetchone()
            if pending:
                return dict(pending)
            stamp = now()
            identity = uuid.uuid4().hex
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (identity, season, tm_id, fm_id, player_id, "queued", "Ожидает загрузки", None, None, stamp, stamp))
        return self.job(identity)

    def job(self, identity: str) -> dict | None:
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identity,)).fetchone()
        return dict(row) if row else None

    def jobs(self, season: str) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM jobs WHERE season=? ORDER BY created_at DESC LIMIT 15", (season,)).fetchall()
        return [dict(row) for row in rows]

    def pending(self) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    def resume_jobs(self):
        with self.connection() as db:
            db.execute("UPDATE jobs SET status='queued',message='Продолжение после перезапуска',updated_at=? WHERE status='running'", (now(),))

    def update_job(self, identity: str, *, status: str | None = None, message: str | None = None,
                   error: str | None = None, player_name: str | None = None, player_id: int | None = None):
        fields = {"updated_at": now()}
        for key, value in (("status", status), ("message", message), ("error", error), ("player_name", player_name), ("player_id", player_id)):
            if value is not None:
                fields[key] = value
        with self.connection() as db:
            db.execute(f"UPDATE jobs SET {','.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), identity))
