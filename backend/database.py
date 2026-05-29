"""
Base de données PostgreSQL — Ligue 1 Pronostics
Variable d'environnement requise : DATABASE_URL (fournie automatiquement par Railway)
"""
import os
import psycopg2
import psycopg2.extras
from datetime import datetime, date

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_db():
    """Retourne une connexion PostgreSQL avec row_factory dict-like."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = False
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
    );

    CREATE TABLE IF NOT EXISTS seasons (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL,
        year_start INTEGER NOT NULL,
        year_end INTEGER NOT NULL,
        is_active INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS matchdays (
        id SERIAL PRIMARY KEY,
        season_id INTEGER NOT NULL REFERENCES seasons(id),
        number INTEGER NOT NULL,
        label TEXT,
        UNIQUE(season_id, number)
    );

    CREATE TABLE IF NOT EXISTS matches (
        id SERIAL PRIMARY KEY,
        matchday_id INTEGER NOT NULL REFERENCES matchdays(id),
        home_team TEXT NOT NULL,
        away_team TEXT NOT NULL,
        kickoff_time TEXT NOT NULL,
        home_score INTEGER,
        away_score INTEGER,
        status TEXT DEFAULT 'scheduled',
        external_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS pronostics (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        match_id INTEGER NOT NULL REFERENCES matches(id),
        home_score INTEGER NOT NULL,
        away_score INTEGER NOT NULL,
        created_at TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
        updated_at TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
        UNIQUE(user_id, match_id)
    );

    CREATE TABLE IF NOT EXISTS score_estimates (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        matchday_id INTEGER NOT NULL REFERENCES matchdays(id),
        estimated_score INTEGER NOT NULL,
        created_at TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
        updated_at TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')),
        UNIQUE(user_id, matchday_id)
    );

    CREATE TABLE IF NOT EXISTS chat_messages (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        message TEXT NOT NULL,
        created_at TEXT DEFAULT (to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS'))
    );
    """)
    conn.commit()
    conn.close()
    print("Base de données PostgreSQL initialisée.")


def get_current_season_years():
    today = date.today()
    if today.month >= 7:
        return today.year, today.year + 1
    else:
        return today.year - 1, today.year


def ensure_season_exists(year_start, year_end):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM seasons WHERE year_start=%s", (year_start,))
    existing = c.fetchone()
    if existing:
        conn.close()
        return existing["id"]
    name = f"Ligue 1 {year_start}/{year_end}"
    c.execute(
        "INSERT INTO seasons (name, year_start, year_end, is_active) VALUES (%s, %s, %s, 0) RETURNING id",
        (name, year_start, year_end)
    )
    season_id = c.fetchone()["id"]
    for i in range(1, 35):
        c.execute(
            "INSERT INTO matchdays (season_id, number, label) VALUES (%s, %s, %s)",
            (season_id, i, f"Journée {i}")
        )
    conn.commit()
    conn.close()
    print(f"Saison {name} créée (id={season_id}).")
    return season_id


def seed_users():
    import hashlib
    conn = get_db()
    c = conn.cursor()
    participants = [
        "Malherbe", "Ben", "Seb", "Coach", "Ricardo",
        "Dreux", "Mathieu", "La Dame blanche", "Le Doubs"
    ]
    for name in participants:
        pwd = hashlib.sha256(name.lower().encode()).hexdigest()
        c.execute("""
            INSERT INTO users (username, password_hash, is_admin)
            VALUES (%s, %s, 0)
            ON CONFLICT (username) DO NOTHING
        """, (name, pwd))
    admin_pwd = hashlib.sha256("admin123".encode()).hexdigest()
    c.execute("""
        INSERT INTO users (username, password_hash, is_admin)
        VALUES (%s, %s, 1)
        ON CONFLICT (username) DO NOTHING
    """, ("admin", admin_pwd))
    conn.commit()
    conn.close()
    print("Utilisateurs créés.")


def seed_active_season():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM seasons WHERE is_active=1")
    active = c.fetchone()
    conn.close()
    if active:
        return
    year_start, year_end = get_current_season_years()
    season_id = ensure_season_exists(year_start, year_end)
    conn = get_db()
    c = conn.cursor()
    conn.cursor().execute("UPDATE seasons SET is_active=1 WHERE id=%s", (season_id,))
    conn.commit()
    conn.close()
    print(f"Saison {year_start}/{year_end} activée.")


if __name__ == "__main__":
    init_db()
    seed_users()
    seed_active_season()
