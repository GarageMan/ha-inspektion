"""Home-Assistant-Core-API: Sensoren setzen, Kilometerstand lesen.

Im Add-on über den Supervisor-Proxy (SUPERVISOR_TOKEN). Außerhalb (z. B. als
Container auf einem anderen Server) über HA_URL + HA_TOKEN (Long-Lived Access Token).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger(__name__)


class HomeAssistant:
    def __init__(self) -> None:
        sup = os.getenv("SUPERVISOR_TOKEN")
        if sup:
            self.base, self.token = "http://supervisor/core/api", sup
        else:
            self.base = os.getenv("HA_URL", "http://homeassistant.local:8123").rstrip("/") + "/api"
            token_file = os.getenv("HA_TOKEN_FILE", "")
            self.token = (open(token_file, encoding="utf-8").read().strip()
                          if token_file and os.path.isfile(token_file) else os.getenv("HA_TOKEN", ""))
        if not self.token:
            raise RuntimeError("Kein Zugriff auf Home Assistant: SUPERVISOR_TOKEN oder HA_TOKEN fehlt.")

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        data = json.dumps(payload, default=str).encode() if payload is not None else None
        req = urllib.request.Request(
            f"{self.base}{path}", data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            return json.loads(body) if body else None

    async def set_state(self, entity_id: str, state: Any, attributes: dict[str, Any]) -> None:
        try:
            await asyncio.to_thread(self._request, "POST", f"/states/{entity_id}",
                                    {"state": "unknown" if state is None else str(state), "attributes": attributes})
        except (urllib.error.URLError, TimeoutError) as exc:
            log.warning("Sensor %s konnte nicht gesetzt werden: %s", entity_id, exc)

    async def get_number(self, entity_id: str) -> int | None:
        if not entity_id:
            return None
        try:
            data = await asyncio.to_thread(self._request, "GET", f"/states/{entity_id}")
            return int(float(data["state"]))
        except (urllib.error.URLError, TimeoutError, KeyError, TypeError, ValueError):
            log.info("Kilometerstand aus %s nicht lesbar", entity_id)
            return None
