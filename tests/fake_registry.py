"""An in-memory ghcr.io for the update checker: token endpoint, paginated tag list, failures.

Behaves like the live registry (spec decision 1): an anonymous pull token from `/token`, the tag
list behind `Authorization: Bearer`, path-only `Link: <...>; rel="next"` pagination, and `403`
with `{"errors": [{"code": "DENIED"}]}` for a bad token. Never touches the network.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import httpx

REPOSITORY = "casta-mere/icloud2nas"  # spelled out, not imported: the fake checks the checker
TOKEN = "fake-pull-token"
TAGS_PATH = f"/v2/{REPOSITORY}/tags/list"


def _denied(status: int) -> httpx.Response:
    return httpx.Response(status, json={"errors": [{"code": "DENIED", "message": "denied"}]})


@dataclass
class FakeRegistry:
    tags: list[str] | None
    page_size: int | None = None  # when set, serve pages of this size with a path-only Link
    token_status: int = 200
    fail: Exception | None = None  # raised by the handler, after `delay`
    delay: asyncio.Event | None = None  # every request waits for it
    omit_tags: bool = False  # answer without a "tags" key at all
    token_requests: int = 0
    requests: list[httpx.URL] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url)
        if request.url.path == "/token":
            self.token_requests += 1
        if self.delay is not None:
            await self.delay.wait()
        if self.fail is not None:
            raise self.fail
        if request.url.path == "/token":
            return self._token(request)
        if request.url.path == TAGS_PATH:
            return self._tags(request)
        return httpx.Response(404, json={"errors": [{"code": "NAME_UNKNOWN"}]})

    def _token(self, request: httpx.Request) -> httpx.Response:
        if self.token_status != 200:
            return _denied(self.token_status)
        if request.url.params.get("scope") != f"repository:{REPOSITORY}:pull":
            return _denied(403)
        return httpx.Response(200, json={"token": TOKEN})

    def _tags(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return _denied(403)
        if self.omit_tags:
            return httpx.Response(200, json={"name": REPOSITORY})
        if self.tags is None:
            return httpx.Response(200, json={"name": REPOSITORY, "tags": None})
        start = 0
        last = request.url.params.get("last")
        if last is not None:
            start = self.tags.index(last) + 1
        size = self.page_size or len(self.tags)
        page = self.tags[start : start + size]
        headers = {}
        if start + size < len(self.tags):
            headers["Link"] = f'<{TAGS_PATH}?last={page[-1]}&n={size}>; rel="next"'
        return httpx.Response(200, json={"name": REPOSITORY, "tags": page}, headers=headers)

    @property
    def tag_requests(self) -> list[httpx.URL]:
        return [url for url in self.requests if url.path == TAGS_PATH]
