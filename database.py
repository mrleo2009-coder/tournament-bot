import aiosqlite
from config import DB_PATH


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                tournaments_played INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS support_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                status TEXT DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tournaments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT,
                start_at TEXT,
                max_players INTEGER DEFAULT 0,
                status TEXT DEFAULT 'open'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS registrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tournament_id INTEGER,
                user_id INTEGER,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tournament_id, user_id)
            )
        """)
        await db.commit()

        # Сидим начальными турнирами, если таблица пустая
        async with db.execute("SELECT COUNT(*) FROM tournaments") as cur:
            count = (await cur.fetchone())[0]
        if count == 0:
            await db.executemany(
                "INSERT INTO tournaments (name, description, start_at, max_players) VALUES (?, ?, ?, ?)",
                [
                    ("Еженедельный кубок", "Классический турнир на выбывание.", "Пятница, 20:00 МСК", 32),
                    ("Рейтинговая лига", "Матчи по швейцарской системе, 5 туров.", "Идёт сейчас", 64),
                ]
            )
            await db.commit()


async def upsert_user(user_id: int, username: str, first_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
        """, (user_id, username, first_name))
        await db.commit()


async def get_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, username, first_name, tournaments_played FROM users WHERE user_id=?",
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def add_tournament_played(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET tournaments_played = tournaments_played + 1 WHERE user_id=?",
            (user_id,)
        )
        await db.commit()


async def create_ticket(user_id: int, text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO support_tickets (user_id, text) VALUES (?, ?)",
            (user_id, text)
        )
        await db.commit()


async def get_all_tournaments():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name, description, start_at, max_players, status FROM tournaments ORDER BY id DESC"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_tournament(tournament_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name, description, start_at, max_players, status FROM tournaments WHERE id=?",
            (tournament_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def count_registrations(tournament_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM registrations WHERE tournament_id=?",
            (tournament_id,)
        ) as cur:
            return (await cur.fetchone())[0]


async def is_registered(tournament_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM registrations WHERE tournament_id=? AND user_id=?",
            (tournament_id, user_id)
        ) as cur:
            return (await cur.fetchone()) is not None


async def register_for_tournament(tournament_id: int, user_id: int) -> str:
    t = await get_tournament(tournament_id)
    if not t:
        return "not_found"
    if t["status"] != "open":
        return "closed"
    if await is_registered(tournament_id, user_id):
        return "already"
    if t["max_players"] and await count_registrations(tournament_id) >= t["max_players"]:
        return "full"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO registrations (tournament_id, user_id) VALUES (?, ?)",
            (tournament_id, user_id)
        )
        await db.commit()
    return "ok"


async def unregister_from_tournament(tournament_id: int, user_id: int) -> str:
    if not await is_registered(tournament_id, user_id):
        return "not_registered"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM registrations WHERE tournament_id=? AND user_id=?",
            (tournament_id, user_id)
        )
        await db.commit()
    return "ok"


async def get_user_registrations(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT t.id, t.name, t.start_at, t.status
            FROM registrations r
            JOIN tournaments t ON t.id = r.tournament_id
            WHERE r.user_id=?
            ORDER BY t.id DESC
        """, (user_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def add_tournament(name: str, description: str, start_at: str, max_players: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO tournaments (name, description, start_at, max_players, status) VALUES (?, ?, ?, ?, 'open')",
            (name, description, start_at, max_players)
        )
        await db.commit()