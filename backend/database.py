"""
Base de données PostgreSQL via pg8000 — driver Python pur, zéro dépendance système.
"""
import os
import pg8000.native
from datetime import date
from urllib.parse import urlparse

def get_pg_params():
    """Parse DATABASE_URL en paramètres de connexion."""
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    p = urlparse(url)
    params = {
        "host": p.hostname,
        "port": p.port or 5432,
        "database": p.path.lstrip("/"),
        "user": p.username,
        "password": p.password,
        "ssl_context": True,  # Railway PostgreSQL requiert SSL
    }
    return params

def get_db():
    """Retourne une connexion pg8000."""
    params = get_pg_params()
    conn = pg8000.native.Connection(**params)
    return conn

class DBConn:
    """Wrapper qui imite l'interface psycopg2 pour compatibilité avec main.py."""

    def __init__(self):
        self._conn = get_db()

    def cursor(self):
        return DBCursor(self._conn)

    def commit(self):
        pass  # pg8000.native est en autocommit par défaut pour les requêtes

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

def get_db_conn():
    return DBConn()

class DBCursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows = []
        self.rowcount = 0

    def execute(self, sql, params=None):
        # Convertir %s en :1, :2... pour pg8000
        converted_sql, converted_params = _convert(sql, params)
        try:
            if converted_params:
                result = self._conn.run(converted_sql, **converted_params)
            else:
                result = self._conn.run(converted_sql)
            self._rows = [dict(zip([col["name"] for col in self._conn.columns], row))
                         for row in (result or [])]
            self.rowcount = len(self._rows)
        except Exception as e:
            print(f"[DB ERROR] {e}\nSQL: {converted_sql}\nParams: {converted_params}")
            raise
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


def _convert(sql, params):
    """Convertit %s en paramètres nommés pg8000 (:p1, :p2...)."""
    if not params:
        return sql, {}
    params = list(params)
    result = []
    named_params = {}
    counter = 1
    i = 0
    while i < len(sql):
        if sql[i:i+2] == '%s':
            name = f"p{counter}"
            result.append(f":{name}")
            named_params[name] = params[counter - 1]
            counter += 1
            i += 2
        else:
            result.append(sql[i])
            i += 1
    return ''.join(result), named_params


def q(conn, sql, params=None):
    c = conn.cursor()
    c.execute(sql, params)
    return c

def qone(conn, sql, params=None):
    return q(conn, sql, params).fetchone()

def qall(conn, sql, params=None):
    return q(conn, sql, params).fetchall()


def _exec(sql, params=None):
    """Exécute une requête DDL ou DML sans retour."""
    conn = get_db()
    try:
        converted_sql, converted_params = _convert(sql, params)
        if converted_params:
            conn.run(converted_sql, **converted_params)
        else:
            conn.run(converted_sql)
    finally:
        conn.close()


def init_db():
    stmts = [
        """CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, is_admin INTEGER DEFAULT 0,
            created_at TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS'))""",
        """CREATE TABLE IF NOT EXISTS seasons (
            id SERIAL PRIMARY KEY, name TEXT NOT NULL,
            year_start INTEGER NOT NULL, year_end INTEGER NOT NULL,
            is_active INTEGER DEFAULT 0)""",
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
    ]
    for stmt in stmts:
        _exec(stmt)
    print("Base de données initialisée.")


def get_current_season_years():
    today = date.today()
    if today.month >= 7:
        return today.year, today.year + 1
    return today.year - 1, today.year


def ensure_season_exists(year_start, year_end):
    conn = get_db_conn()
    existing = qone(conn, "SELECT id FROM seasons WHERE year_start=%s", (year_start,))
    if existing:
        conn.close()
        return existing["id"]
    name = f"Ligue 1 {year_start}/{year_end}"
    c = conn.cursor()
    c.execute("INSERT INTO seasons (name,year_start,year_end,is_active) VALUES (%s,%s,%s,0) RETURNING id",
              (name, year_start, year_end))
    season_id = c.fetchone()["id"]
    for i in range(1, 35):
        c.execute("INSERT INTO matchdays (season_id,number,label) VALUES (%s,%s,%s)",
                  (season_id, i, f"Journée {i}"))
    conn.close()
    print(f"Saison {name} créée (id={season_id}).")
    return season_id


def seed_users():
    import hashlib
    conn = get_db_conn()
    participants = ["Malherbe","Ben","Seb","Coach","Ricardo","Dreux","Mathieu","La Dame blanche","Le Doubs"]
    for name in participants:
        pwd = hashlib.sha256(name.lower().encode()).hexdigest()
        q(conn, "INSERT INTO users (username,password_hash,is_admin) VALUES (%s,%s,0) ON CONFLICT(username) DO NOTHING", (name, pwd))
    admin_pwd = hashlib.sha256("admin123".encode()).hexdigest()
    q(conn, "INSERT INTO users (username,password_hash,is_admin) VALUES (%s,%s,1) ON CONFLICT(username) DO NOTHING", ("admin", admin_pwd))
    conn.close()
    print("Utilisateurs créés.")


def seed_active_season():
    conn = get_db_conn()
    active = qone(conn, "SELECT id FROM seasons WHERE is_active=1")
    conn.close()
    if active:
        return
    year_start, year_end = get_current_season_years()
    season_id = ensure_season_exists(year_start, year_end)
    conn = get_db_conn()
    q(conn, "UPDATE seasons SET is_active=1 WHERE id=%s", (season_id,))
    conn.close()
    print(f"Saison {year_start}/{year_end} activée.")
