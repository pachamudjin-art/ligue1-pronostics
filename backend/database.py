import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "pronostics.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        is_admin INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS seasons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        year_start INTEGER NOT NULL,
        year_end INTEGER NOT NULL,
        is_active INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS matchdays (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        season_id INTEGER NOT NULL,
        number INTEGER NOT NULL,
        label TEXT,
        FOREIGN KEY (season_id) REFERENCES seasons(id),
        UNIQUE(season_id, number)
    );

    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        matchday_id INTEGER NOT NULL,
        home_team TEXT NOT NULL,
        away_team TEXT NOT NULL,
        kickoff_time TEXT NOT NULL,
        home_score INTEGER,
        away_score INTEGER,
        status TEXT DEFAULT 'scheduled',
        external_id INTEGER,
        FOREIGN KEY (matchday_id) REFERENCES matchdays(id)
    );

    CREATE TABLE IF NOT EXISTS pronostics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        match_id INTEGER NOT NULL,
        home_score INTEGER NOT NULL,
        away_score INTEGER NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (match_id) REFERENCES matches(id),
        UNIQUE(user_id, match_id)
    );

    CREATE TABLE IF NOT EXISTS score_estimates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        matchday_id INTEGER NOT NULL,
        estimated_score INTEGER NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (matchday_id) REFERENCES matchdays(id),
        UNIQUE(user_id, matchday_id)
    );

    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    """)

    conn.commit()
    conn.close()
    print("Base de données initialisée.")

def seed_users():
    """Crée les 9 participants + admin s'ils n'existent pas."""
    import hashlib
    conn = get_db()
    c = conn.cursor()

    participants = [
        "Malherbe", "Ben", "Seb", "Coach", "Ricardo",
        "Dreux", "Mathieu", "La Dame blanche", "Le Doubs"
    ]

    for name in participants:
        pwd = hashlib.sha256(name.lower().encode()).hexdigest()
        try:
            c.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 0)",
                (name, pwd)
            )
        except sqlite3.IntegrityError:
            pass

    # Admin : username=admin, password=admin123 (à changer !)
    admin_pwd = hashlib.sha256("admin123".encode()).hexdigest()
    try:
        c.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
            ("admin", admin_pwd)
        )
    except sqlite3.IntegrityError:
        pass

    conn.commit()
    conn.close()
    print("Utilisateurs créés.")

def seed_season_2025_2026():
    """Crée la saison 2025/2026 pour les tests."""
    conn = get_db()
    c = conn.cursor()

    existing = c.execute("SELECT id FROM seasons WHERE year_start=2025").fetchone()
    if existing:
        conn.close()
        return existing["id"]

    c.execute(
        "INSERT INTO seasons (name, year_start, year_end, is_active) VALUES (?, ?, ?, 1)",
        ("Ligue 1 2025/2026", 2025, 2026)
    )
    season_id = c.lastrowid

    for i in range(1, 35):
        c.execute(
            "INSERT INTO matchdays (season_id, number, label) VALUES (?, ?, ?)",
            (season_id, i, f"Journée {i}")
        )

    conn.commit()
    conn.close()
    print(f"Saison 2025/2026 créée (id={season_id}).")
    return season_id

if __name__ == "__main__":
    init_db()
    seed_users()
    seed_season_2025_2026()
