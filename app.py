"""Company B: a deliberately small, independently deployable Python support desk."""

import hashlib
import os
import secrets
import sqlite3
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["low", "normal", "high", "critical"]
STATIC = Path(__file__).parent / "static"


def now():
    return datetime.now(UTC).isoformat()


class TicketInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    subject: str = Field(min_length=3, max_length=160)
    description: str = Field(min_length=5, max_length=3000)
    requester: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=5, max_length=200, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    severity: Severity = "normal"


class FaultInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    kind: Literal["none", "priority-inversion"]
    expected_fault: str | None = Field(default=None, alias="expectedFault")
    expected_fault_id: str | None = Field(
        default=None, alias="expectedFaultId", min_length=1, max_length=100
    )


def create_app(database_path=None, demo_faults=None, sandbox_token=None):
    path = database_path or os.getenv("DATABASE_PATH", "./data/company-b.sqlite")
    enabled = demo_faults if demo_faults is not None else os.getenv("ENABLE_DEMO_FAULTS") == "true"
    token = sandbox_token if sandbox_token is not None else os.getenv("SANDBOX_TOKEN", "")
    Path(path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def database():
        db = sqlite3.connect(path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @asynccontextmanager
    async def lifespan(_app):
        with database() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS tickets(
                    id TEXT PRIMARY KEY, subject TEXT NOT NULL, description TEXT NOT NULL,
                    requester TEXT NOT NULL, email TEXT NOT NULL, severity TEXT NOT NULL,
                    priority TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('open','resolved')),
                    createdAt TEXT NOT NULL, resolvedAt TEXT, dueAt TEXT NOT NULL,
                    requestKey TEXT NOT NULL UNIQUE, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT OR IGNORE INTO settings VALUES('fault','none');
                INSERT OR IGNORE INTO settings VALUES('faultId','');
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,message TEXT NOT NULL,createdAt TEXT NOT NULL);
            """)
        yield

    app = FastAPI(title="Company B · Support desk", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            from urllib.parse import urlsplit

            origin = request.headers.get("origin")
            try:
                allowed_origin = not origin or urlsplit(origin).netloc == request.headers.get(
                    "host"
                )
            except ValueError:
                allowed_origin = False
            if not allowed_origin:
                return JSONResponse({"error": "Cross-origin changes are not allowed."}, 403)
            if "application/json" not in request.headers.get("content-type", ""):
                return JSONResponse({"error": "Use application/json."}, 415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def handle_error(_request, error):
        return JSONResponse({"error": error.detail}, error.status_code)

    def fault(db):
        return (
            db.execute("SELECT value FROM settings WHERE key='fault'").fetchone()[0]
            if enabled
            else "none"
        )

    def classify(severity, current_fault):
        return (
            "low" if severity == "critical" and current_fault == "priority-inversion" else severity
        )

    def ticket_json(row):
        return {
            key: value for key, value in dict(row).items() if key not in {"payload", "requestKey"}
        }

    @app.get("/api/health")
    def health():
        with database() as db:
            current = fault(db)
            fault_id = db.execute("SELECT value FROM settings WHERE key='faultId'").fetchone()[0]
        return JSONResponse(
            {
                "status": "healthy" if current == "none" else "degraded",
                "service": "company-b",
                "version": "1.0.0",
                "database": "connected",
                "fault": current,
                "faultId": fault_id,
                "timestamp": now(),
            },
            200 if current == "none" else 503,
        )

    @app.get("/api/classify")
    def classification(severity: Severity):
        with database() as db:
            return {"severity": severity, "priority": classify(severity, fault(db))}

    @app.get("/api/overview")
    def overview():
        with database() as db:
            tickets = [
                ticket_json(r) for r in db.execute("SELECT * FROM tickets ORDER BY createdAt DESC")
            ]
            events = [dict(r) for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 40")]
            current = fault(db)
        open_tickets = [t for t in tickets if t["status"] == "open"]
        return {
            "tickets": tickets,
            "events": events,
            "fault": current,
            "metrics": {
                "open": len(open_tickets),
                "resolved": len(tickets) - len(open_tickets),
                "critical": sum(t["priority"] == "critical" for t in open_tickets),
                "overdue": sum(t["dueAt"] < now() for t in open_tickets),
            },
        }

    @app.post("/api/tickets", status_code=201)
    def create_ticket(
        body: TicketInput, idempotency_key: str = Header(..., min_length=8, max_length=100)
    ):
        payload = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM tickets WHERE requestKey=?", (idempotency_key,)
            ).fetchone()
            if existing:
                if existing["payload"] != payload:
                    raise HTTPException(
                        409, "That request key was already used for a different ticket."
                    )
                return JSONResponse(ticket_json(existing), 200)
            priority = classify(body.severity, fault(db))
            hours = {"critical": 1, "high": 4, "normal": 24, "low": 72}[priority]
            ticket_id = str(uuid.uuid4())
            due = (datetime.now(UTC) + timedelta(hours=hours)).isoformat()
            db.execute(
                "INSERT INTO tickets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ticket_id,
                    body.subject,
                    body.description,
                    body.requester,
                    body.email,
                    body.severity,
                    priority,
                    "open",
                    now(),
                    None,
                    due,
                    idempotency_key,
                    payload,
                ),
            )
            db.execute(
                "INSERT INTO events(type,message,createdAt) VALUES(?,?,?)",
                ("ticket.created", f"Ticket {ticket_id[:8]} opened · {priority} priority", now()),
            )
            return ticket_json(
                db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            )

    @app.post("/api/tickets/{ticket_id}/resolve")
    def resolve_ticket(ticket_id: str):
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            ticket = db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            if not ticket:
                raise HTTPException(404, "Ticket not found.")
            if ticket["status"] == "open":
                db.execute(
                    "UPDATE tickets SET status='resolved',resolvedAt=? WHERE id=?",
                    (now(), ticket_id),
                )
                db.execute(
                    "INSERT INTO events(type,message,createdAt) VALUES(?,?,?)",
                    ("ticket.resolved", f"Ticket {ticket_id[:8]} resolved", now()),
                )
            return ticket_json(
                db.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
            )

    @app.post("/api/demo/fault")
    def demo_fault(body: FaultInput, authorization: str = Header(default="")):
        if not enabled:
            raise HTTPException(404, "Demo fault controls are disabled.")
        if not token or not secrets.compare_digest(authorization, f"Bearer {token}"):
            raise HTTPException(401, "A sandbox connector token is required.")
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            if body.expected_fault and fault(db) != body.expected_fault:
                raise HTTPException(409, "The fault changed after the proposal was captured.")
            current_id = db.execute("SELECT value FROM settings WHERE key='faultId'").fetchone()[0]
            if body.expected_fault_id and current_id != body.expected_fault_id:
                raise HTTPException(409, "The fault changed after the proposal was captured.")
            next_fault_id = str(uuid.uuid4())
            db.execute("UPDATE settings SET value=? WHERE key='fault'", (body.kind,))
            db.execute("UPDATE settings SET value=? WHERE key='faultId'", (next_fault_id,))
            # Reclassify open demo tickets after a rollback; never alter customer content.
            if body.kind == "none":
                for row in db.execute(
                    "SELECT id,severity,createdAt FROM tickets WHERE status='open'"
                ).fetchall():
                    hours = {"critical": 1, "high": 4, "normal": 24, "low": 72}[row["severity"]]
                    due = (
                        datetime.fromisoformat(row["createdAt"]) + timedelta(hours=hours)
                    ).isoformat()
                    db.execute(
                        "UPDATE tickets SET priority=severity,dueAt=? WHERE id=?", (due, row["id"])
                    )
            db.execute(
                "INSERT INTO events(type,message,createdAt) VALUES(?,?,?)",
                ("demo.fault", f"Demo fault changed to {body.kind}", now()),
            )
        return {"fault": body.kind, "faultId": next_fault_id}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app


app = create_app()
