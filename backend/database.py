"""
Base de données PostgreSQL via psycopg2 avec pool de connexions.
"""
import os
import psycopg2
import psycopg2.extras
import psycopg2.pool
from datetime import date

_pool = None

def get_pg_url():
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url

def init_pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=get_pg_url())
        print("Pool de connexions initialisé.")

def get_db():
    global _pool
    if _pool is None:
        init_pool()
    conn = _pool.getconn()
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    conn.autocommit = False
    return conn

def release_db(conn):
    global _pool
    if _pool and conn:
        try: _pool.putconn(conn)
        except: pass

def q(conn, sql, params=None):
    c = conn.cursor()
    c.execute(sql, params or ())
    return c

def qone(conn, sql, params=None): return q(conn, sql, params).fetchone()
def qall(conn, sql, params=None): return q(conn, sql, params).fetchall()

get_db_conn = get_db

def init_db():
    conn = get_db()
    c = conn.cursor()
    stmts = [
        """CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, is_admin INTEGER DEFAULT 0,
            theme TEXT DEFAULT 'ligue1',
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))""",
        """CREATE TABLE IF NOT EXISTS seasons (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL,
            year_start INTEGER NOT NULL, year_end INTEGER NOT NULL,
            is_active INTEGER DEFAULT 0,
            competition_type TEXT DEFAULT 'league',
            api_code TEXT DEFAULT 'FL1')""",
        """CREATE TABLE IF NOT EXISTS matchdays (
            id SERIAL PRIMARY KEY, season_id INTEGER NOT NULL REFERENCES seasons(id),
            number INTEGER NOT NULL, label TEXT, UNIQUE(season_id, number))""",
        """CREATE TABLE IF NOT EXISTS matches (
            id SERIAL PRIMARY KEY, matchday_id INTEGER NOT NULL REFERENCES matchdays(id),
            home_team TEXT NOT NULL, away_team TEXT NOT NULL,
            kickoff_time TEXT NOT NULL, home_score INTEGER, away_score INTEGER,
            status TEXT DEFAULT 'scheduled', external_id INTEGER)""",
        """CREATE TABLE IF NOT EXISTS pronostics (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            match_id INTEGER NOT NULL REFERENCES matches(id),
            home_score INTEGER NOT NULL, away_score INTEGER NOT NULL,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'),
            updated_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'),
            UNIQUE(user_id, match_id))""",
        """CREATE TABLE IF NOT EXISTS score_estimates (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            matchday_id INTEGER NOT NULL REFERENCES matchdays(id),
            estimated_score INTEGER NOT NULL,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'),
            updated_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'),
            UNIQUE(user_id, matchday_id))""",
        """CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            message TEXT NOT NULL,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))""",
        # Table pronostics podium (Coupe du Monde etc.)
        """CREATE TABLE IF NOT EXISTS podium_pronostics (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            season_id INTEGER NOT NULL REFERENCES seasons(id),
            rank1 TEXT NOT NULL,
            rank2 TEXT NOT NULL,
            rank3 TEXT NOT NULL,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'),
            updated_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'),
            UNIQUE(user_id, season_id))""",
        # Table Hall of Fame
        """CREATE TABLE IF NOT EXISTS hall_of_fame (
            id SERIAL PRIMARY KEY,
            type TEXT NOT NULL,
            num INTEGER NOT NULL,
            saison TEXT NOT NULL,
            joueur TEXT NOT NULL,
            points INTEGER,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))""",
        """CREATE TABLE IF NOT EXISTS user_notifications (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) UNIQUE,
            email TEXT,
            telegram_chat_id TEXT,
            notify_24h INTEGER DEFAULT 1,
            notify_2h INTEGER DEFAULT 1,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))""",
        """CREATE TABLE IF NOT EXISTS chat_reactions (
            id SERIAL PRIMARY KEY,
            message_id INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            emoji TEXT NOT NULL,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'),
            UNIQUE(message_id, user_id, emoji))""",
        """CREATE TABLE IF NOT EXISTS user_notifications (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) UNIQUE,
            email TEXT,
            telegram_chat_id TEXT,
            notify_24h INTEGER DEFAULT 1,
            notify_2h INTEGER DEFAULT 1,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))""",
        """CREATE TABLE IF NOT EXISTS push_subscriptions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))""",
        """CREATE TABLE IF NOT EXISTS notification_log (
            id SERIAL PRIMARY KEY,
            matchday_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            sent_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'),
            UNIQUE(matchday_id, type))""",
        # Table résultat podium réel
        """CREATE TABLE IF NOT EXISTS podium_results (
            id SERIAL PRIMARY KEY,
            season_id INTEGER NOT NULL REFERENCES seasons(id) UNIQUE,
            rank1 TEXT NOT NULL,
            rank2 TEXT NOT NULL,
            rank3 TEXT NOT NULL,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))""",
    ]
    for stmt in stmts:
        c.execute(stmt)
    # Migrations colonnes existantes
    migrations = [
        "ALTER TABLE seasons ADD COLUMN IF NOT EXISTS competition_type TEXT DEFAULT 'league'",
        "ALTER TABLE seasons ADD COLUMN IF NOT EXISTS api_code TEXT DEFAULT 'FL1'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS theme TEXT DEFAULT 'ligue1'",
        """CREATE TABLE IF NOT EXISTS hall_of_fame (
            id SERIAL PRIMARY KEY, type TEXT NOT NULL, num INTEGER NOT NULL,
            saison TEXT NOT NULL, joueur TEXT NOT NULL, points INTEGER,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))""",
        """CREATE TABLE IF NOT EXISTS user_notifications (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) UNIQUE,
            email TEXT,
            telegram_chat_id TEXT,
            notify_24h INTEGER DEFAULT 1,
            notify_2h INTEGER DEFAULT 1,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))""",
        """CREATE TABLE IF NOT EXISTS chat_reactions (
            id SERIAL PRIMARY KEY,
            message_id INTEGER NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            emoji TEXT NOT NULL,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'),
            UNIQUE(message_id, user_id, emoji))""",
        """CREATE TABLE IF NOT EXISTS user_notifications (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) UNIQUE,
            email TEXT,
            telegram_chat_id TEXT,
            notify_24h INTEGER DEFAULT 1,
            notify_2h INTEGER DEFAULT 1,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))""",
        """CREATE TABLE IF NOT EXISTS push_subscriptions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))""",
        """CREATE TABLE IF NOT EXISTS notification_log (
            id SERIAL PRIMARY KEY,
            matchday_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            sent_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'),
            UNIQUE(matchday_id, type))""",
    ]
    for m in migrations:
        try: c.execute(m)
        except: pass
    conn.commit()
    release_db(conn)
    print("Base de données initialisée.")

def get_current_season_years():
    today = date.today()
    if today.month >= 7:
        return today.year, today.year + 1
    return today.year - 1, today.year

def ensure_season_exists(year_start, year_end, name=None, competition_type='league', api_code='FL1', nb_journees=34):
    conn = get_db()
    existing = qone(conn, "SELECT id FROM seasons WHERE year_start=%s AND competition_type=%s", (year_start, competition_type))
    if existing:
        release_db(conn)
        return existing["id"]
    if not name:
        name = f"Ligue 1 {year_start}/{year_end}"
    c = conn.cursor()
    c.execute("INSERT INTO seasons (name,year_start,year_end,is_active,competition_type,api_code) VALUES (%s,%s,%s,0,%s,%s) RETURNING id",
              (name, year_start, year_end, competition_type, api_code))
    season_id = c.fetchone()["id"]
    for i in range(1, nb_journees + 1):
        c.execute("INSERT INTO matchdays (season_id,number,label) VALUES (%s,%s,%s)",
                  (season_id, i, f"Journée {i}"))
    conn.commit()
    release_db(conn)
    print(f"Compétition {name} créée (id={season_id}).")
    return season_id

def seed_users():
    import hashlib
    conn = get_db()
    participants = ["Malherbe","Ben","Seb","Coach","Ricardo","Dreux","Mathieu","La Dame blanche","Le Doubs"]
    for name in participants:
        pwd = hashlib.sha256(name.lower().encode()).hexdigest()
        q(conn, "INSERT INTO users (username,password_hash,is_admin) VALUES (%s,%s,0) ON CONFLICT(username) DO NOTHING", (name, pwd))
    admin_pwd = hashlib.sha256("admin123".encode()).hexdigest()
    q(conn, "INSERT INTO users (username,password_hash,is_admin) VALUES (%s,%s,1) ON CONFLICT(username) DO NOTHING", ("admin", admin_pwd))
    conn.commit()
    release_db(conn)
    print("Utilisateurs créés.")

def seed_active_season():
    conn = get_db()
    active = qone(conn, "SELECT id FROM seasons WHERE is_active=1")
    release_db(conn)
    if active:
        return
    year_start, year_end = get_current_season_years()
    season_id = ensure_season_exists(year_start, year_end)
    conn = get_db()
    q(conn, "UPDATE seasons SET is_active=1 WHERE id=%s", (season_id,))
    conn.commit()
    release_db(conn)
    print(f"Saison {year_start}/{year_end} activée.")
