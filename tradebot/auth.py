"""Accounts, sessions and login protection (standard library only).

* Passwords: PBKDF2-HMAC-SHA256 (600k iterations), per-user random salt, constant-time compare. The plain password is never stored or logged.
* Sessions: random 256-bit token in an HttpOnly, SameSite=Lax cookie (Secure over HTTPS). Only a SHA-256 of the token is stored, so a leaked
  database cannot be replayed. Idle timeout 12 h, absolute 30 days with "remember me", 24 h without.
* Brute force: 5 wrong passwords from one address or for one name locks that address / name for 15 minutes.
* Roles: owner (everything) and viewer (read-only dashboard, no trading controls, no settings, no AI spending).
* First run on a public address: creating the owner account needs a one-time setup code printed in the server console and saved to
  data/setup_code.txt, so a stranger who finds the URL first cannot claim the instance.
"""
import hashlib, hmac, os, re, secrets, sqlite3, threading, time

ITER = 600_000
IDLE_S = 12 * 3600
SHORT_S = 24 * 3600
LONG_S = 30 * 86400
MAX_FAILS, LOCK_S, WINDOW_S = 5, 15 * 60, 15 * 60
NAME_RE = re.compile(r"^[A-Za-z0-9._@-]{3,40}$")
COMMON = {"password", "password1", "password123", "qwerty123", "letmein123", "1234567890", "iloveyou12", "admin12345", "welcome123", "sentinel123", "trading123"}


class AuthError(ValueError):
    pass


def _hash(password, salt, iters=ITER):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters).hex()


def check_password_strength(pw, name=""):
    if len(pw) < 10:
        raise AuthError("Use at least 10 characters.")
    if len(pw) > 200:
        raise AuthError("That password is too long (200 characters max).")
    if pw.lower() in COMMON or (name and pw.lower() == name.lower()) or len(set(pw)) < 4:
        raise AuthError("That password is too easy to guess. Try a few unrelated words.")


class Auth:
    def __init__(self, db, lock, data_dir):
        self.db, self.lock, self.data_dir = db, lock, data_dir
        self.fails = {}                       # key -> [count, first_ts, locked_until]
        self.flock = threading.Lock()
        with self.lock:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users(name TEXT PRIMARY KEY, salt BLOB, pw TEXT, iters INTEGER, role TEXT, created INTEGER, last_login INTEGER);
            CREATE TABLE IF NOT EXISTS sessions(h TEXT PRIMARY KEY, user TEXT, created INTEGER, last INTEGER, expires INTEGER, ip TEXT, ua TEXT);
            """)
            self.db.commit()
        self._dummy = (os.urandom(16), _hash("dummy-password-for-timing", b"x" * 16))

    # ------------------------------------------------------------------ users
    def has_users(self):
        with self.lock:
            return self.db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def users(self):
        with self.lock:
            return [dict(name=r[0], role=r[1], created=r[2], last_login=r[3]) for r in self.db.execute("SELECT name,role,created,last_login FROM users ORDER BY created")]

    def role_of(self, name):
        with self.lock:
            r = self.db.execute("SELECT role FROM users WHERE name=?", (name,)).fetchone()
            return r[0] if r else None

    def create_user(self, name, password, role="viewer"):
        name = (name or "").strip()
        if not NAME_RE.match(name):
            raise AuthError("Username: 3-40 letters, numbers, dot, dash, underscore or @.")
        if role not in ("owner", "viewer"):
            raise AuthError("Unknown role.")
        check_password_strength(password, name)
        salt = os.urandom(16)
        with self.lock:
            if self.db.execute("SELECT 1 FROM users WHERE lower(name)=lower(?)", (name,)).fetchone():
                raise AuthError("That username is taken.")
            self.db.execute("INSERT INTO users VALUES(?,?,?,?,?,?,NULL)", (name, salt, _hash(password, salt), ITER, role, int(time.time())))
            self.db.commit()

    def set_password(self, name, password):
        check_password_strength(password, name)
        salt = os.urandom(16)
        with self.lock:
            self.db.execute("UPDATE users SET salt=?, pw=?, iters=? WHERE name=?", (salt, _hash(password, salt), ITER, name))
            self.db.execute("DELETE FROM sessions WHERE user=?", (name,))          # a password change signs out every device
            self.db.commit()

    def delete_user(self, name):
        with self.lock:
            if self.role_of(name) == "owner" and self.db.execute("SELECT COUNT(*) FROM users WHERE role='owner'").fetchone()[0] <= 1:
                raise AuthError("You can't remove the only owner.")
            self.db.execute("DELETE FROM users WHERE name=?", (name,))
            self.db.execute("DELETE FROM sessions WHERE user=?", (name,))
            self.db.commit()

    def verify(self, name, password):
        with self.lock:
            r = self.db.execute("SELECT name,salt,pw,iters FROM users WHERE lower(name)=lower(?)", (name or "",)).fetchone()
        salt, want, iters = (r[1], r[2], r[3]) if r else (self._dummy[0], self._dummy[1], ITER)       # same work for unknown names: no username probing by timing
        got = _hash(password or "", salt, iters)
        ok = hmac.compare_digest(got, want) and r is not None
        return r[0] if ok else None

    # ------------------------------------------------------------------ brute-force guard
    def _keys(self, ip, name):
        return ["ip:" + (ip or "?"), "u:" + (name or "").lower()]

    def locked_for(self, ip, name):
        now = time.time()
        with self.flock:
            return max([0] + [self.fails[k][2] - now for k in self._keys(ip, name) if k in self.fails and self.fails[k][2] > now])

    def note_fail(self, ip, name):
        now = time.time()
        with self.flock:
            for k in self._keys(ip, name):
                f = self.fails.get(k)
                if not f or now - f[1] > WINDOW_S:
                    f = self.fails[k] = [0, now, 0]
                f[0] += 1
                if f[0] >= MAX_FAILS:
                    f[2] = now + LOCK_S
            if len(self.fails) > 5000:
                self.fails = {k: v for k, v in self.fails.items() if v[2] > now}

    def note_ok(self, ip, name):
        with self.flock:
            for k in self._keys(ip, name):
                self.fails.pop(k, None)

    # ------------------------------------------------------------------ sessions
    @staticmethod
    def _th(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def login(self, name, password, ip="", ua="", remember=False):
        wait = self.locked_for(ip, name)
        if wait > 0:
            raise AuthError("Too many attempts. Try again in %d minutes." % (int(wait // 60) + 1))
        who = self.verify(name, password)
        if not who:
            self.note_fail(ip, name)
            raise AuthError("Wrong username or password.")
        self.note_ok(ip, name)
        return who, self._new_session(who, ip, ua, remember)

    def _new_session(self, user, ip, ua, remember):
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        life = LONG_S if remember else SHORT_S
        with self.lock:
            self.db.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?)", (self._th(token), user, now, now, now + life, ip[:64], ua[:160]))
            self.db.execute("UPDATE users SET last_login=? WHERE name=?", (now, user))
            self.db.execute("DELETE FROM sessions WHERE expires < ?", (now,))
            self.db.commit()
        return token, life

    def session(self, token):
        """-> (user, role) or None. Slides the idle window."""
        if not token:
            return None
        now = int(time.time())
        h = self._th(token)
        with self.lock:
            r = self.db.execute("SELECT s.user, s.last, s.expires, u.role FROM sessions s JOIN users u ON u.name = s.user WHERE s.h=?", (h,)).fetchone()
            if not r:
                return None
            if r[2] < now or now - r[1] > IDLE_S:
                self.db.execute("DELETE FROM sessions WHERE h=?", (h,))
                self.db.commit()
                return None
            if now - r[1] > 60:
                self.db.execute("UPDATE sessions SET last=? WHERE h=?", (now, h))
                self.db.commit()
            return r[0], r[3]

    def logout(self, token):
        with self.lock:
            self.db.execute("DELETE FROM sessions WHERE h=?", (self._th(token or ""),))
            self.db.commit()

    def revoke_all(self, user, keep_token=None):
        with self.lock:
            if keep_token:
                self.db.execute("DELETE FROM sessions WHERE user=? AND h<>?", (user, self._th(keep_token)))
            else:
                self.db.execute("DELETE FROM sessions WHERE user=?", (user,))
            self.db.commit()

    def session_count(self, user):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM sessions WHERE user=? AND expires>?", (user, int(time.time()))).fetchone()[0]

    # ------------------------------------------------------------------ first-run setup code
    def setup_code(self):
        """One-time code needed to create the first owner when the instance is reachable from outside."""
        path = os.path.join(self.data_dir, "setup_code.txt")
        try:
            with open(path) as f:
                code = f.read().strip()
            if code:
                return code
        except OSError:
            pass
        code = "-".join(secrets.token_hex(2) for _ in range(3))
        os.makedirs(self.data_dir, exist_ok=True)
        with open(path, "w") as f:
            f.write(code + "\n")
        return code

    def clear_setup_code(self):
        try:
            os.remove(os.path.join(self.data_dir, "setup_code.txt"))
        except OSError:
            pass
