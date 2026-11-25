import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import time

import httpx
from transfermarkt_api.exceptions import NotFound, TransfermarktError, UpstreamTimeout

from .domain import Season, combine
from .providers.fotmob import FotMobProvider
from .providers.transfermarkt import TransfermarktProvider
from .store import Store


log = logging.getLogger(__name__)


class SourcePaused(ValueError):
    pass


class PacedTransport(httpx.AsyncBaseTransport):
    """Share source pacing and Retry-After cooldown across all adapter requests."""
    def __init__(self):
        self.upstream = httpx.AsyncHTTPTransport(retries=1)
        self.gate = asyncio.Lock()
        self.next_request = {}
        self.paused_until = {}

    async def handle_async_request(self, request):
        host = request.url.host
        async with self.gate:
            cooldown = self.paused_until.get(host, 0) - time.time()
            if cooldown > 0:
                raise SourcePaused(f"Источник временно ограничил запросы. Повторите загрузку через {int(cooldown) + 1} сек.")
            await asyncio.sleep(max(0, self.next_request.get(host, 0) - time.monotonic()))
            self.next_request[host] = time.monotonic() + 0.35
            response = await self.upstream.handle_async_request(request)
            if response.status_code == 429:
                retry = response.headers.get("retry-after", "60")
                try:
                    seconds = float(retry)
                except ValueError:
                    try:
                        stamp = parsedate_to_datetime(retry)
                        if stamp.tzinfo is None:
                            stamp = stamp.replace(tzinfo=timezone.utc)
                        seconds = (stamp - datetime.now(timezone.utc)).total_seconds()
                    except (ValueError, TypeError, OverflowError):
                        seconds = 60
                self.paused_until[host] = time.time() + max(60, min(seconds, 86400))
            return response

    async def aclose(self):
        await self.upstream.aclose()


def source_error(error: Exception) -> str:
    if isinstance(error, TransfermarktError):
        status = error.status_code
        if isinstance(error, NotFound) or status == 404:
            return "Transfermarkt: игрок не найден. Проверьте ссылку."
        if isinstance(error, UpstreamTimeout):
            return "Transfermarkt не ответил вовремя. Повторите загрузку позже."
        if status == 429:
            return "Transfermarkt ограничил запросы. Подождите и повторите загрузку."
        if status in (401, 403):
            return "Transfermarkt временно не разрешает доступ. Повторите загрузку позже."
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        source = "FotMob" if "fotmob" in error.request.url.host else "Transfermarkt"
        if status == 404:
            return f"{source}: игрок или сезон не найден. Проверьте ссылку."
        if status == 429:
            return f"{source} ограничил запросы. Подождите и повторите загрузку."
        if status in (401, 403):
            return f"{source} временно не разрешает доступ. Повторите загрузку позже."
        return f"{source} временно недоступен (HTTP {status}). Повторите загрузку позже."
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "Источник не ответил вовремя. Повторите загрузку позже."
    if isinstance(error, httpx.RequestError):
        return "Не удалось подключиться к источнику. Проверьте соединение и повторите загрузку."
    if isinstance(error, ValueError):
        return str(error)[:500]
    # SDK exceptions expose their source details in logs; keep the UI actionable.
    return "Не удалось загрузить данные Transfermarkt. Проверьте ссылку и повторите загрузку позже."


class Importer:
    def __init__(self, store: Store, client: httpx.AsyncClient, *, tm=None, fm=None):
        self.store = store
        self.tm = tm or TransfermarktProvider(client)
        self.fm = fm or FotMobProvider(client)
        self.wakeup = asyncio.Event()
        self.worker = None

    async def start(self):
        self.store.resume_jobs()
        self.worker = asyncio.create_task(self.run())
        self.wakeup.set()

    async def stop(self):
        if self.worker:
            self.worker.cancel()
            try:
                await self.worker
            except asyncio.CancelledError:
                pass

    def enqueue(self, season: str, tm_id: str, fm_id: str | None = None, player_id: int | None = None):
        job = self.store.create_job(season, tm_id, fm_id, player_id)
        self.wakeup.set()
        return job

    async def run(self):
        while True:
            self.wakeup.clear()
            jobs = self.store.pending()
            if not jobs:
                await self.wakeup.wait()
                continue
            for job in jobs:
                await self.process(job)

    async def process(self, job: dict):
        self.store.update_job(job["id"], status="running", message="Загружаем Transfermarkt")
        previous = self.store.find_player(job["season"], job["transfermarkt_id"])
        try:
            async with asyncio.timeout(240):
                season = Season.parse(job["season"])
                tm = await self.tm.load(job["transfermarkt_id"], season)
                name = tm["profile"]["name"]
                self.store.update_job(job["id"], message="Загружаем оценки FotMob", player_name=name)
                # Keep a previously confirmed identity stable on refresh.
                fm_id = job.get("fotmob_id") or (previous or {}).get("fotmob_id")
                try:
                    fm = await self.fm.load({**tm["profile"], "season_competitions": tm["competitions"]}, season, player_id=fm_id)
                except Exception as error:
                    log.warning("FotMob failed for %s: %s", job["transfermarkt_id"], error)
                    fm = {"status": "unavailable", "competitions": [], "candidates": [],
                          "warnings": [source_error(error)]}
                if previous:
                    old_tm, old_fm = self.store.raw(previous["id"])
                    if fm.get("status") == "unavailable" and old_fm.get("status") == "matched":
                        fm = {**old_fm, "warnings": [*fm.get("warnings", []),
                              "FotMob не обновился: сохранены оценки предыдущей успешной загрузки."]}
                    if tm.get("market_value") is None and old_tm.get("market_value"):
                        tm["market_value"] = old_tm["market_value"]
                        tm.setdefault("warnings", []).append("История стоимости не обновилась: показана ранее сохранённая оценка с указанной датой.")
                snapshot = combine(tm, fm)
                player_id = self.store.save_player(job["season"], job["transfermarkt_id"], snapshot, tm, fm)
                self.store.update_job(job["id"], status="completed", message="Игрок сохранён" if fm.get("status") == "matched" else "Игрок сохранён. Проверьте статус FotMob в карточке.",
                                      player_id=player_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.exception("Import failed for Transfermarkt %s", job["transfermarkt_id"])
            message = source_error(error)
            self.store.fail_player((previous or {}).get("id"), message)
            self.store.update_job(job["id"], status="failed", error=message, message="Загрузка не завершена")
