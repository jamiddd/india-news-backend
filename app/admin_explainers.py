"""Admin panel for Explainers: pose a question (ad hoc or from the trending
suggestions strip), generate an answer with Claude (+ optional Sarvam
narration), review/edit, and publish. Same session/CSRF/nav pattern as
admin_daily_brief.py / admin_timelines.py.
"""
from __future__ import annotations

import html

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_session import (
    credentials_match,
    custom_select,
    form_fields,
    layout,
    login_form,
    session_csrf,
    set_session_cookie,
    verify,
)
from app.database import AsyncSessionLocal, get_db
from app.models import Explainer
from app.services import explainer as explainer_service
from app.services.daily_brief_select import CORE_CATEGORIES, FALLBACK_CATEGORIES
from app.services.timeline_audio import is_configured as audio_is_configured
from app.services.trending import get_trending_terms

router = APIRouter(prefix="/admin/explainers")
TITLE = "Explainers"

CATEGORIES = CORE_CATEGORIES + FALLBACK_CATEGORIES
DEPTHS = [("quick", "Quick (~2m)"), ("standard", "Standard (~5m)"), ("deep", "Deep dive (~9m)")]
VOICES = [("shubh", "Shubh (male)"), ("simran", "Simran (female)")]

STATUS_LABELS = {
    "draft": "Draft",
    "generating": "Generating…",
    "ready_for_review": "Ready for review",
    "published": "Published",
    "archived": "Archived",
}
STATUS_CLASSES = {
    "draft": "meta",
    "generating": "meta",
    "ready_for_review": "",
    "published": "done",
    "archived": "meta",
}

GENERATE_CONFIRM = (
    "Generate this explainer? This calls Claude (roughly Rs 7) — add narration and it also calls "
    "Sarvam (roughly Rs 3 more) — and takes a couple of minutes."
)


@router.get("/login", response_class=HTMLResponse)
async def login_page():
    return login_form(TITLE, "/admin/explainers/login")


@router.post("/login")
async def login(request: Request):
    fields = await form_fields(request)
    if not credentials_match(fields):
        return layout(TITLE, "<h1>Sign in failed</h1><p class=danger>Invalid credentials.</p>"
                             "<a href='/admin/explainers/login'>Try again</a>")
    response = RedirectResponse("/admin/explainers", status_code=303)
    set_session_cookie(response, request)
    return response


def _status_pill(status: str) -> str:
    label = STATUS_LABELS.get(status, status)
    cls = STATUS_CLASSES.get(status, "")
    return f"<span class={cls}>{html.escape(label)}</span>" if cls else html.escape(label)


def _row_actions(row: Explainer) -> str:
    if row.status == "draft":
        return (f"<a href='/admin/explainers/{row.id}/review'>Edit</a> &middot; "
                f"<a href='/admin/explainers/{row.id}/review'>Generate</a>")
    if row.status == "generating":
        return f"<a href='/admin/explainers/{row.id}/review'>View progress</a>"
    if row.status == "ready_for_review":
        return f"<a href='/admin/explainers/{row.id}/review'>Review</a>"
    if row.status == "published":
        return f"<a href='/admin/explainers/{row.id}/review'>View</a> &middot; <a href='/admin/explainers/{row.id}/review'>Archive</a>"
    if row.status == "archived":
        return f"<a href='/admin/explainers/{row.id}/review'>Restore</a>"
    return ""


def _dashboard_table(rows: list[Explainer]) -> str:
    if not rows:
        return "<p class=meta>No explainers yet — ask one below.</p>"
    trs = "".join(
        f"<tr><td>{html.escape(row.question)}</td><td class=meta>{html.escape(row.category)}</td>"
        f"<td>{_status_pill(row.status)}</td>"
        f"<td class=meta>{row.updated_at:%Y-%m-%d %H:%M}</td>"
        f"<td>{_row_actions(row)}</td></tr>"
        for row in rows
    )
    return f"<table><tr><th>Question</th><th>Category</th><th>Status</th><th>Updated</th><th>Actions</th></tr>{trs}</table>"


def _suggestions_strip(terms: list[tuple[str, int]]) -> str:
    if not terms:
        return ""
    cards = "".join(
        f"<div class=task style='display:inline-block;width:240px;vertical-align:top;margin-right:12px'>"
        f"<p style='margin:0 0 8px'>{html.escape(term)}</p>"
        f"<p class=meta style='margin:0 0 8px'>{count} search{'es' if count != 1 else ''} this week</p>"
        f"<a href='/admin/explainers/new?q={html.escape(term)}'>Use this →</a></div>"
        for term, count in terms
    )
    return (
        "<h2>Suggested — trending searches</h2>"
        f"<div style='overflow-x:auto;white-space:nowrap;margin-bottom:20px'>{cards}</div>"
    )


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    if not session_csrf(request):
        return RedirectResponse("/admin/explainers/login", status_code=303)
    rows = (await db.execute(select(Explainer).order_by(desc(Explainer.updated_at)).limit(100))).scalars().all()
    terms = await get_trending_terms(5)
    body = (
        "<h1>Explainers</h1>"
        "<p class=meta>Questions the newsroom has asked, and what the AI generated for them.</p>"
        f"<p><a href='/admin/explainers/new'><button>+ New question</button></a></p>"
        f"{_suggestions_strip(terms)}"
        f"{_dashboard_table(rows)}"
    )
    return layout(TITLE, body, current="/admin/explainers")


@router.get("/new", response_class=HTMLResponse)
async def new_question_form(request: Request, q: str = ""):
    csrf = session_csrf(request)
    if not csrf:
        return RedirectResponse("/admin/explainers/login", status_code=303)
    category_options = custom_select("category", [(c, c.title()) for c in CATEGORIES])
    depth_options = custom_select("depth", DEPTHS, selected="standard")
    body = (
        "<h1>New explainer question</h1>"
        f"<form method=post action='/admin/explainers'>"
        f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
        f"<label>Question<input name=question required value='{html.escape(q)}'></label>"
        f"<label>Category</label>{category_options}"
        f"<label>Depth</label>{depth_options}"
        "<label>Angle or notes for the writer AI (optional)"
        "<textarea name=admin_notes rows=3 placeholder="
        "\"e.g. focus on impact for importers, avoid jargon, mention RBI's likely response\"></textarea></label>"
        "<button>Save as draft</button>"
        "</form>"
    )
    return layout(TITLE, body, current="/admin/explainers")


@router.post("")
async def create(request: Request):
    fields = await form_fields(request)
    verify(request, fields)
    question = (fields.get("question") or "").strip()
    if not question:
        return RedirectResponse("/admin/explainers/new", status_code=303)
    category = fields.get("category") or CATEGORIES[0]
    depth = fields.get("depth") or "standard"
    admin_notes = (fields.get("admin_notes") or "").strip() or None
    row = Explainer(question=question, category=category, depth=depth, admin_notes=admin_notes)
    async with AsyncSessionLocal() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
        new_id = row.id
    return RedirectResponse(f"/admin/explainers/{new_id}/review", status_code=303)


def _section_block(explainer_id: int, index: int, section: dict, csrf: str) -> str:
    return (
        f"<div class=task>"
        f"<h2>{html.escape(section['heading'])}</h2>"
        f"<p>{html.escape(section['body'])}</p>"
        f"<form method=post action='/admin/explainers/{explainer_id}/regenerate-section' style='margin:0'>"
        f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
        f"<input type=hidden name=section_index value='{index}'>"
        f"<button data-busy-msg='Regenerating this section…'>Regenerate section</button>"
        f"</form></div>"
    )


def _sources_block(sources: list[dict] | None) -> str:
    if not sources:
        return ""
    items = "".join(
        f"<li>{html.escape(s['title'])} <span class=meta>&middot; {html.escape(s['outlet'])}</span></li>"
        for s in sources
    )
    return f"<h2>Sources used ({len(sources)})</h2><ul>{items}</ul>"


@router.get("/{explainer_id}/review", response_class=HTMLResponse)
async def review(explainer_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    csrf = session_csrf(request)
    if not csrf:
        return RedirectResponse("/admin/explainers/login", status_code=303)
    row = (await db.execute(select(Explainer).where(Explainer.id == explainer_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Explainer not found")

    status = await explainer_service.get_status(explainer_id)
    running = status.get("state") == "generating" or await explainer_service.in_progress(explainer_id)
    refresh = "<script>setTimeout(function(){location.reload()},8000)</script>" if running else ""

    header = (
        f"<h1>{html.escape(row.question)}</h1>"
        f"<p class=meta>{_status_pill(row.status)} &middot; {html.escape(row.category)} &middot; {row.depth}</p>"
    )

    if row.status in ("draft", "generating"):
        control = (
            "<button disabled>Generating&hellip;</button>" if running else
            (f"<form method=post action='/admin/explainers/{explainer_id}/generate' "
             f"onsubmit=\"return confirm('{html.escape(GENERATE_CONFIRM, quote=True)}')\">"
             f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
             "<label class=opt><input type=checkbox name=narrate value=1> Also generate audio narration</label>"
             f"<label>Voice</label>{custom_select('voice', VOICES, selected='shubh')}"
             + ("" if audio_is_configured() else "<p class=meta>Audio isn't configured on this server; "
                                                    "narration would be skipped.</p>")
             + "<button>Generate explainer</button></form>")
        )
        body = header + control + (
            f"<p class=danger>Last attempt failed: {html.escape(row.error)}</p>" if row.error else ""
        ) + refresh
        return layout(TITLE, body, current="/admin/explainers")

    # ready_for_review / published / archived: show the generated content.
    audio_block = (
        f"<audio controls preload=none src='{html.escape(row.audio_url, quote=True)}'></audio>"
        f"<p class=meta>{(row.audio_duration_seconds or 0) // 60}:{(row.audio_duration_seconds or 0) % 60:02d} "
        f"&middot; {html.escape(row.voice or '')}</p>"
        if row.audio_url else ""
    )
    quick_answer_block = (
        f"<div class=task><h2>Quick answer</h2><p>{html.escape(row.quick_answer or '')}</p></div>"
    )
    sections_block = "".join(
        _section_block(explainer_id, i, s, csrf) for i, s in enumerate(row.sections or [])
    )
    sources_block = _sources_block(row.sources)

    actions = []
    csrf_input = f"<input type=hidden name=csrf value='{html.escape(csrf)}'>"
    if row.status == "ready_for_review":
        actions.append(
            f"<form method=post action='/admin/explainers/{explainer_id}/generate' style='display:inline' "
            f"onsubmit=\"return confirm('Regenerate the whole explainer? This discards the current draft.')\">"
            f"{csrf_input}<button>Regenerate all</button></form>"
        )
        actions.append(
            f"<form method=post action='/admin/explainers/{explainer_id}/publish' style='display:inline'>"
            f"{csrf_input}<button>Publish explainer</button></form>"
        )
    elif row.status == "published":
        actions.append(
            f"<form method=post action='/admin/explainers/{explainer_id}/archive' style='display:inline'>"
            f"{csrf_input}<button>Archive</button></form>"
        )
    elif row.status == "archived":
        actions.append(
            f"<form method=post action='/admin/explainers/{explainer_id}/restore' style='display:inline'>"
            f"{csrf_input}<button>Restore &amp; republish</button></form>"
        )

    body = header + audio_block + quick_answer_block + sections_block + sources_block + "".join(actions)
    return layout(TITLE, body, current="/admin/explainers")


@router.post("/{explainer_id}/generate")
async def generate(explainer_id: int, request: Request, background_tasks: BackgroundTasks):
    fields = await form_fields(request)
    verify(request, fields)
    if await explainer_service.in_progress(explainer_id):
        return RedirectResponse(f"/admin/explainers/{explainer_id}/review", status_code=303)
    narrate = fields.get("narrate") == "1"
    voice = fields.get("voice") or "shubh"
    await explainer_service.set_status(explainer_id, "generating", "starting")
    background_tasks.add_task(explainer_service.run_build_task, explainer_id, narrate=narrate, voice=voice)
    return RedirectResponse(f"/admin/explainers/{explainer_id}/review", status_code=303)


@router.get("/{explainer_id}/status")
async def status_json(explainer_id: int, request: Request):
    if not session_csrf(request):
        raise HTTPException(status_code=401, detail="Not signed in")
    status = await explainer_service.get_status(explainer_id)
    running = await explainer_service.in_progress(explainer_id) or status.get("state") == "generating"
    return JSONResponse({
        "explainer_id": explainer_id,
        "state": "generating" if running else status.get("state", "idle"),
        "message": status.get("message", ""),
    })


@router.post("/{explainer_id}/regenerate-section")
async def regenerate_section(explainer_id: int, request: Request):
    fields = await form_fields(request)
    verify(request, fields)
    try:
        section_index = int(fields.get("section_index", ""))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid section_index")
    await explainer_service.regenerate_section(explainer_id, section_index)
    return RedirectResponse(f"/admin/explainers/{explainer_id}/review", status_code=303)


@router.post("/{explainer_id}/publish")
async def publish(explainer_id: int, request: Request):
    fields = await form_fields(request)
    verify(request, fields)
    await explainer_service.publish(explainer_id)
    return RedirectResponse(f"/admin/explainers/{explainer_id}/review", status_code=303)


@router.post("/{explainer_id}/archive")
async def archive(explainer_id: int, request: Request):
    fields = await form_fields(request)
    verify(request, fields)
    await explainer_service.archive(explainer_id)
    return RedirectResponse(f"/admin/explainers/{explainer_id}/review", status_code=303)


@router.post("/{explainer_id}/restore")
async def restore(explainer_id: int, request: Request):
    fields = await form_fields(request)
    verify(request, fields)
    await explainer_service.restore(explainer_id)
    return RedirectResponse(f"/admin/explainers/{explainer_id}/review", status_code=303)
