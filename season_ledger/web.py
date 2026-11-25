from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .domain import CATEGORIES, Season, current_season, fotmob_id, transfermarkt_id
from .importer import Importer, PacedTransport
from .store import Store


ROOT = Path(__file__).parent


class SeasonInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_year: StrictInt
    calendar: StrictBool = False


class PlayerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(max_length=2048)
    season: str = Field(max_length=12)
    fotmob_id: str | None = Field(default=None, max_length=512)


class RefreshInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fotmob_id: str | None = Field(default=None, max_length=512)


def create_app(db_path: str | Path | None = None, *, tm=None, fm=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        store = Store(db_path or os.environ.get("SEASON_LEDGER_DB", "data/seasons.sqlite3"))
        async with httpx.AsyncClient(transport=PacedTransport(), timeout=httpx.Timeout(40, connect=15),
                                     follow_redirects=False, headers={"User-Agent": "SeasonLedger/0.1"}) as client:
            importer = Importer(store, client, tm=tm, fm=fm)
            app.state.store, app.state.importer = store, importer
            await importer.start()
            try:
                yield
            finally:
                await importer.stop()

    app = FastAPI(title="Сезон · REAL B’DOR", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=ROOT / "static", check_dir=False), name="static")
    templates = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape())

    @app.exception_handler(ValueError)
    async def validation_error(request: Request, error: ValueError):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": str(error)}, status_code=400)

    def store() -> Store:
        return app.state.store

    def season_or_404(key: str) -> Season:
        season = Season.parse(key)
        if not store().has_season(key):
            raise HTTPException(404, "Сезон не найден. Сначала создайте его.")
        return season

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return templates.get_template("index.html").render()

    @app.get("/api/seasons")
    async def seasons():
        return {"seasons": store().seasons(), "current": current_season().key}

    @app.post("/api/seasons", status_code=201)
    async def add_season(body: SeasonInput):
        return store().add_season(Season(body.start_year, body.calendar))

    @app.get("/api/seasons/{key}")
    async def season_data(key: str):
        season = season_or_404(key)
        return {"season": season.as_dict(), "players": store().players(key),
                "jobs": store().jobs(key), "categories": CATEGORIES}

    @app.post("/api/players", status_code=202)
    async def add_player(body: PlayerInput):
        season_or_404(body.season)
        tm_id, fm_id = transfermarkt_id(body.url), fotmob_id(body.fotmob_id)
        existing = store().find_player(body.season, tm_id)
        if existing:
            return {"job": {"id": f"existing-{existing['id']}", "status": "completed",
                            "message": "Этот игрок уже добавлен в выбранный сезон.",
                            "player_id": existing["id"], "player_name": existing["name"]}, "duplicate": True}
        return {"job": app.state.importer.enqueue(body.season, tm_id, fm_id)}

    @app.get("/api/jobs/{identity}")
    async def get_job(identity: str):
        job = store().job(identity)
        if job is None:
            raise HTTPException(404, "Загрузка не найдена.")
        return {"job": job}

    @app.post("/api/players/{identity}/refresh", status_code=202)
    async def refresh(identity: int, body: RefreshInput):
        player = store().player(identity)
        if player is None:
            raise HTTPException(404, "Игрок не найден.")
        return {"job": app.state.importer.enqueue(player["season"], player["transfermarkt_id"],
                                                  fotmob_id(body.fotmob_id), identity)}

    @app.delete("/api/players/{identity}", status_code=204)
    async def delete(identity: int):
        if not store().delete_player(identity):
            raise HTTPException(404, "Игрок не найден.")
        return Response(status_code=204)

    return app


app = create_app()
