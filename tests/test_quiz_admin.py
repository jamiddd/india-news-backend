"""Daily Quiz review (app/quiz_admin.py) and Quiz bank (app/quiz_bank_admin.py)
JSON APIs behind the admin SPA.

What matters: nothing reads without a session or writes without the CSRF
header, a draft can only be approved/rejected while it is a draft, the
reviewer's edits are validated like generated content, and a Claude-drafted
bank question is handed back for review rather than saved.
"""
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import quiz_admin, quiz_bank_admin
from app.config import settings
from app.database import get_db
from app.main import app
from app.models import DailyQuiz, QuizBankQuestion
from app.services.daily_games import IST

USERNAME = "reviewer"
PASSWORD = "correct-horse"


@pytest.fixture(autouse=True)
def admin_credentials(monkeypatch):
    monkeypatch.setattr(settings, "POLL_ADMIN_USERNAME", USERNAME)
    monkeypatch.setattr(settings, "POLL_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setattr(settings, "POLL_SESSION_SECRET", "test-secret")


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for model in (DailyQuiz, QuizBankQuestion):
            await conn.run_sync(model.__table__.create)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.session_factory = Session
        yield c
    app.dependency_overrides.clear()


async def sign_in(client) -> dict:
    r = await client.post("/admin/api/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 200
    csrf = (await client.get("/admin/api/session")).json()["csrf"]
    return {"X-CSRF-Token": csrf}


def make_questions(prefix="Q"):
    return [{
        "id": i + 1,
        "question": f"{prefix} question {i + 1}?",
        "options": [f"{prefix}{i}a", f"{prefix}{i}b", f"{prefix}{i}c", f"{prefix}{i}d"],
        "correct_index": i % 4,
        "explanation": f"Because {i}.",
    } for i in range(5)]


def as_edit(questions):
    return [{"question": q["question"], "options": q["options"], "correctIndex": q["correct_index"],
             "explanation": q["explanation"]} for q in questions]


async def seed_quiz(client, status="draft") -> int:
    async with client.session_factory() as session:
        quiz = DailyQuiz(puzzle_date=datetime.now(IST).date(), questions=make_questions(),
                         source="ai", status=status)
        session.add(quiz)
        await session.commit()
        return quiz.id


@pytest.fixture
def fake_generate(monkeypatch):
    calls = []

    async def fake(day, db=None):
        calls.append(day)
        return make_questions("New"), "ai"

    monkeypatch.setattr(quiz_admin, "generate_quiz", fake)
    return calls


class TestAuth:
    @pytest.mark.parametrize("path", ["/admin/api/quiz", "/admin/api/quiz-bank"])
    async def test_reads_need_a_session(self, client, path):
        assert (await client.get(path)).status_code == 401

    @pytest.mark.parametrize("method,path,body", [
        ("post", "/admin/api/quiz/generate", None),
        ("post", "/admin/api/quiz/1/approve", {"questions": []}),
        ("post", "/admin/api/quiz/1/reject", None),
        ("post", "/admin/api/quiz-bank", {"question": "x"}),
        ("post", "/admin/api/quiz-bank/generate", {}),
        ("post", "/admin/api/quiz-bank/1/toggle", None),
        ("post", "/admin/api/quiz-bank/bulk", {"ids": [1], "action": "delete"}),
        ("delete", "/admin/api/quiz-bank/1", None),
    ])
    async def test_writes_need_the_csrf_header(self, client, method, path, body):
        await sign_in(client)
        kwargs = {"json": body} if body is not None else {}
        r = await client.request(method.upper(), path, **kwargs)
        assert r.status_code == 403, path

    async def test_old_list_endpoint_is_gone(self, client):
        await sign_in(client)
        r = await client.get("/admin/quiz-bank/api/list")
        assert "application/json" not in r.headers["content-type"]


class TestDailyQuiz:
    async def test_no_quiz_yet(self, client):
        await sign_in(client)
        d = (await client.get("/admin/api/quiz")).json()
        assert d["quiz"] is None
        assert d["questionCount"] == 5 and d["optionCount"] == 4

    async def test_generate_when_missing_creates_a_draft(self, client, fake_generate):
        h = await sign_in(client)
        r = await client.post("/admin/api/quiz/generate", headers=h)
        assert r.status_code == 200
        q = r.json()["quiz"]
        assert q["status"] == "draft" and q["editable"] and not q["servedToReaders"]
        assert q["questions"][0]["question"] == "New question 1?"
        assert q["publishAt"]
        assert len(fake_generate) == 1

    async def test_generation_failure_is_a_readable_error(self, client, monkeypatch):
        async def boom(day, db=None):
            raise RuntimeError("Claude is down")
        monkeypatch.setattr(quiz_admin, "generate_quiz", boom)
        h = await sign_in(client)
        r = await client.post("/admin/api/quiz/generate", headers=h)
        assert r.status_code == 502
        assert "Draft generation failed: Claude is down" in r.json()["detail"]

    async def test_approve_saves_edits_and_publishes(self, client):
        quiz_id = await seed_quiz(client)
        h = await sign_in(client)
        edits = as_edit(make_questions())
        edits[0]["question"] = "  Edited?  "
        edits[0]["correctIndex"] = 3
        r = await client.post(f"/admin/api/quiz/{quiz_id}/approve", json={"questions": edits}, headers=h)
        assert r.status_code == 200, r.text
        q = r.json()["quiz"]
        assert q["status"] == "approved" and q["servedToReaders"] and not q["editable"]
        assert q["questions"][0]["question"] == "Edited?"
        assert q["questions"][0]["correctIndex"] == 3
        assert q["approvedAt"]
        async with client.session_factory() as session:
            stored = await session.get(DailyQuiz, quiz_id)
            assert stored.questions[0]["correct_index"] == 3

    @pytest.mark.parametrize("mutate,message", [
        (lambda e: e[1].update(question=" "), "Question 2 is empty"),
        (lambda e: e[2]["options"].__setitem__(1, ""), "Question 3 needs 4 non-empty options"),
        (lambda e: e[3]["options"].__setitem__(1, e[3]["options"][0].upper()), "Question 4 has duplicate options"),
        (lambda e: e[4].update(correctIndex=4), "Question 5 has no valid correct answer"),
        (lambda e: e.pop(), "exactly 5 questions"),
    ])
    async def test_approve_rejects_bad_edits(self, client, mutate, message):
        quiz_id = await seed_quiz(client)
        h = await sign_in(client)
        edits = as_edit(make_questions())
        mutate(edits)
        r = await client.post(f"/admin/api/quiz/{quiz_id}/approve", json={"questions": edits}, headers=h)
        assert r.status_code == 400
        assert message in r.json()["detail"]
        async with client.session_factory() as session:
            assert (await session.get(DailyQuiz, quiz_id)).status == "draft"

    async def test_only_a_draft_can_be_approved_or_rejected(self, client):
        quiz_id = await seed_quiz(client, status="approved")
        h = await sign_in(client)
        r = await client.post(f"/admin/api/quiz/{quiz_id}/approve",
                              json={"questions": as_edit(make_questions())}, headers=h)
        assert r.status_code == 409
        assert (await client.post(f"/admin/api/quiz/{quiz_id}/reject", headers=h)).status_code == 409

    async def test_reject(self, client):
        quiz_id = await seed_quiz(client)
        h = await sign_in(client)
        r = await client.post(f"/admin/api/quiz/{quiz_id}/reject", headers=h)
        assert r.status_code == 200
        assert r.json()["quiz"]["status"] == "rejected"

    async def test_missing_quiz_is_404(self, client):
        h = await sign_in(client)
        assert (await client.post("/admin/api/quiz/999/reject", headers=h)).status_code == 404

    async def test_regenerating_an_approved_quiz_makes_it_a_draft_again(self, client, fake_generate):
        await seed_quiz(client, status="approved")
        h = await sign_in(client)
        q = (await client.post("/admin/api/quiz/generate", headers=h)).json()["quiz"]
        assert q["status"] == "draft" and q["approvedAt"] is None
        assert q["questions"][0]["question"] == "New question 1?"


async def seed_bank(client, **kwargs) -> int:
    fields = {"question": "Capital of Assam?", "options": ["Dispur", "Shillong", "Imphal", "Agartala"],
              "correct_index": 0, "explanation": "Dispur is the capital.", "category": "geography"}
    fields.update(kwargs)
    async with client.session_factory() as session:
        item = QuizBankQuestion(**fields)
        session.add(item)
        await session.commit()
        return item.id


class TestQuizBank:
    async def test_list_counts_and_filters(self, client):
        await seed_bank(client)
        await seed_bank(client, question="Inactive?", is_active=False, category="history")
        await sign_in(client)
        d = (await client.get("/admin/api/quiz-bank")).json()
        assert d["counts"] == {"total": 2, "active": 1, "inactive": 1}
        assert d["categories"] == ["geography", "history"]
        assert {i["question"] for i in d["items"]} == {"Capital of Assam?", "Inactive?"}
        assert d["items"][0]["correctIndex"] in (0,)
        only = (await client.get("/admin/api/quiz-bank", params={"activeOnly": "true", "limit": 1})).json()
        assert [i["question"] for i in only["items"]] == ["Capital of Assam?"]
        assert only["hasMore"] is False

    async def test_add(self, client):
        h = await sign_in(client)
        r = await client.post("/admin/api/quiz-bank", headers=h, json={
            "question": " Largest planet? ", "options": ["Jupiter", "Saturn", "Earth", "Mars"],
            "correctIndex": 0, "explanation": "By mass.", "category": " "})
        assert r.status_code == 200, r.text
        item = r.json()
        assert item["question"] == "Largest planet?" and item["category"] is None and item["isActive"]

    @pytest.mark.parametrize("body,message", [
        ({"question": "", "options": ["a", "b", "c", "d"]}, "Question can't be empty."),
        ({"question": "Q?", "options": ["a", "b", "c"]}, "Need all 4 options filled in."),
        ({"question": "Q?", "options": ["a", "A", "c", "d"]}, "Options must be distinct."),
        ({"question": "Q?", "options": ["a", "b", "c", "d"], "correctIndex": 7}, "Pick a valid correct answer."),
    ])
    async def test_add_validation(self, client, body, message):
        h = await sign_in(client)
        r = await client.post("/admin/api/quiz-bank", headers=h, json=body)
        assert r.status_code == 400
        assert r.json()["detail"] == message

    async def test_generate_returns_a_draft_without_saving(self, client, monkeypatch):
        seen = {}

        async def fake(system, user, **kw):
            seen["user"] = user
            return {"question": "Who wrote Gitanjali?", "options": ["Tagore", "Premchand", "Naidu", "Bose"],
                    "correct_index": 0, "explanation": "Rabindranath Tagore."}
        monkeypatch.setattr(quiz_bank_admin, "call_claude_json", fake)
        h = await sign_in(client)
        r = await client.post("/admin/api/quiz-bank/generate", headers=h, json={"category": "literature"})
        assert r.status_code == 200
        assert r.json()["draft"]["correctIndex"] == 0
        assert r.json()["draft"]["category"] == "literature"
        assert "literature" in seen["user"]
        assert (await client.get("/admin/api/quiz-bank")).json()["counts"]["total"] == 0

    async def test_generate_failures(self, client, monkeypatch):
        h = await sign_in(client)

        async def none(*a, **kw):
            return None
        monkeypatch.setattr(quiz_bank_admin, "call_claude_json", none)
        r = await client.post("/admin/api/quiz-bank/generate", headers=h, json={})
        assert r.status_code == 502 and "Claude request failed" in r.json()["detail"]

        async def bad(*a, **kw):
            return {"question": "Q?", "options": ["a", "a", "b", "c"], "correct_index": 0}
        monkeypatch.setattr(quiz_bank_admin, "call_claude_json", bad)
        r = await client.post("/admin/api/quiz-bank/generate", headers=h, json={})
        assert r.status_code == 422 and "failed validation" in r.json()["detail"]

    async def test_toggle_delete_and_bulk(self, client):
        a = await seed_bank(client)
        b = await seed_bank(client, question="Second?")
        h = await sign_in(client)
        r = await client.post(f"/admin/api/quiz-bank/{a}/toggle", headers=h)
        assert r.json()["isActive"] is False
        r = await client.post("/admin/api/quiz-bank/bulk", headers=h, json={"ids": [a, b], "action": "activate"})
        assert r.json()["updated"] == 2
        assert (await client.get("/admin/api/quiz-bank")).json()["counts"]["active"] == 2
        assert (await client.delete(f"/admin/api/quiz-bank/{a}", headers=h)).status_code == 200
        assert (await client.delete(f"/admin/api/quiz-bank/{a}", headers=h)).status_code == 404
        await client.post("/admin/api/quiz-bank/bulk", headers=h, json={"ids": [b], "action": "delete"})
        assert (await client.get("/admin/api/quiz-bank")).json()["counts"]["total"] == 0
