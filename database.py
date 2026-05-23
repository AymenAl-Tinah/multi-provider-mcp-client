import sqlite3
import hashlib
import os
import secrets
import json
from contextlib import contextmanager

DB_PATH = "mcp_client.db"

def hash_password(password: str, salt: bytes = None) -> tuple[bytes, bytes]:
    """Hash a password using PBKDF2."""
    if salt is None:
        salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt,
        100000
    )
    return key, salt

def verify_password(password: str, stored_hash: bytes, salt: bytes) -> bool:
    """Verify a password against a stored hash and salt."""
    key, _ = hash_password(password, salt)
    return secrets.compare_digest(key, stored_hash)

@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()

def init_db():
    """Initialize the database schema."""
    with get_db() as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash BLOB NOT NULL,
                salt BLOB NOT NULL,
                config_data TEXT DEFAULT '{}',
                settings_data TEXT DEFAULT '{}'
            )
        ''')
        db.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')

def create_user(username: str, password: str) -> bool:
    """Create a new user. Returns True if successful, False if username exists."""
    password_hash, salt = hash_password(password)
    try:
        with get_db() as db:
            db.execute(
                'INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)',
                (username, password_hash, salt)
            )
        return True
    except sqlite3.IntegrityError:
        return False

def authenticate_user(username: str, password: str) -> dict | None:
    """Authenticate a user and return their data if successful."""
    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        if user and verify_password(password, user['password_hash'], user['salt']):
            return dict(user)
    return None

def create_session(user_id: int) -> str:
    """Create a new session for a user and return the token."""
    token = secrets.token_hex(32)
    with get_db() as db:
        db.execute(
            'INSERT INTO sessions (token, user_id) VALUES (?, ?)',
            (token, user_id)
        )
    return token

def get_user_from_session(token: str) -> dict | None:
    """Retrieve user data based on a session token."""
    with get_db() as db:
        row = db.execute('''
            SELECT u.* FROM users u
            JOIN sessions s ON u.id = s.user_id
            WHERE s.token = ?
        ''', (token,)).fetchone()
        if row:
            return dict(row)
    return None

def destroy_session(token: str):
    """Destroy a session."""
    with get_db() as db:
        db.execute('DELETE FROM sessions WHERE token = ?', (token,))

def get_user_config(user_id: int) -> dict:
    """Get the user's config.json data."""
    with get_db() as db:
        row = db.execute('SELECT config_data FROM users WHERE id = ?', (user_id,)).fetchone()
        if row and row['config_data']:
            try:
                return json.loads(row['config_data'])
            except:
                return {}
    return {}

def save_user_config(user_id: int, data: dict):
    """Save the user's config.json data."""
    config_str = json.dumps(data, indent=4)
    with get_db() as db:
        db.execute('UPDATE users SET config_data = ? WHERE id = ?', (config_str, user_id))

def get_user_settings(user_id: int) -> dict:
    """Get the user's settings.json data."""
    with get_db() as db:
        row = db.execute('SELECT settings_data FROM users WHERE id = ?', (user_id,)).fetchone()
        if row and row['settings_data']:
            try:
                return json.loads(row['settings_data'])
            except:
                return {}
    return {}

def save_user_settings(user_id: int, data: dict):
    """Save the user's settings.json data."""
    settings_str = json.dumps(data, indent=4)
    with get_db() as db:
        db.execute('UPDATE users SET settings_data = ? WHERE id = ?', (settings_str, user_id))
