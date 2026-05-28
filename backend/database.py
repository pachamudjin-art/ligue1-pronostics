import sqlite3
import os
from datetime import datetime, date

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

def get_current_season_years():
    """Détermine la saison en cours selon la date : du 1er juillet N au 30 juin N+1."""
    today = date.today()
    if today.month >= 7:
        return today.year, today.year + 1
    else:
        return today.year - 1, today.year

def ensure_season_exists(year_start, year_end):
    """Crée la saison si elle n'existe pas encore. Retourne son id."""
    conn = get_db()
    c = conn.cursor()
    existing = c.execute("SELECT id FROM seasons WHERE year_start=?", (year_start,)).fetchone()
    if existing:
        conn.close()
        return existing["id"]
    name = f"Ligue 1 {year_start}/{year_end}"
    c.execute(
        "INSERT INTO seasons (name, year_start, year_end, is_active) VALUES (?, ?, ?, 0)",
        (name, year_start, year_end)
    )
    season_id = c.lastrowid
    for i in range(1, 35):
        c.execute(
            "INSERT INTO matchdays (season_id, number, label) VALUES (?, ?, ?)",
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
        try:
            c.execute("INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 0)", (name, pwd))
        except sqlite3.IntegrityError:
            pass
    admin_pwd = hashlib.sha256("admin123".encode()).hexdigest()
    try:
        c.execute("INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)", ("admin", admin_pwd))
    except sqlite3.IntegrityError:
        pass
    conn.commit()
    conn.close()
    print("Utilisateurs créés.")

def seed_active_season():
    """Crée la saison courante et la marque active si aucune saison active n'existe."""
    conn = get_db()
    active = conn.execute("SELECT id FROM seasons WHERE is_active=1").fetchone()
    conn.close()
    if active:
        return
    year_start, year_end = get_current_season_years()
    season_id = ensure_season_exists(year_start, year_end)
    conn = get_db()
    conn.execute("UPDATE seasons SET is_active=1 WHERE id=?", (season_id,))
    conn.commit()
    conn.close()
    print(f"Saison {year_start}/{year_end} activée.")

if __name__ == "__main__":
    init_db()
    seed_users()
    seed_active_season()
