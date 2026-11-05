"""Image generation through an OpenAI-compatible images endpoint.

Honest scope: this generates raster images when a provider key is configured. It
does not generate 3D models, video or audio - those need providers that are not
wired here, and pretending otherwise would be a lie.
"""
from __future__ import annotations

import base64
from typing import Any

import httpx


class MediaError(RuntimeError):
    pass


class ImageProvider:
    def __init__(self, api_key: str, *, model: str = "gpt-image-1",
                 base_url: str = "https://api.openai.com/v1", timeout: float = 60.0,
                 transport: Any | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def generate(self, prompt: str, *, size: str = "1024x1024") -> bytes:
        if not self.enabled:
            raise MediaError("no image provider configured (set OMNIFORGE_MEDIA_API_KEY)")
        if not prompt.strip():
            raise MediaError("an image prompt is required")
        payload = {"model": self.model, "prompt": prompt[:4000], "size": size, "n": 1}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        async with httpx.AsyncClient(**kwargs) as client:
            response = await client.post(f"{self.base_url}/images/generations",
                                         json=payload, headers=headers)
        if response.status_code >= 400:
            raise MediaError(f"image endpoint returned {response.status_code}: "
                             f"{response.text[:200]}")
        data = response.json().get("data") or []
        if not data:
            raise MediaError("image endpoint returned no data")
        first = data[0]
        if first.get("b64_json"):
            return base64.b64decode(first["b64_json"])
        if first.get("url"):
            async with httpx.AsyncClient(**kwargs) as client:
                image = await client.get(first["url"])
            if image.status_code >= 400:
                raise MediaError(f"could not download the generated image ({image.status_code})")
            return image.content
        raise MediaError("image endpoint returned neither b64_json nor url")


class SpeechProvider:
    """Text to speech through an OpenAI-compatible /audio/speech endpoint."""

    def __init__(self, api_key: str, *, model: str = "tts-1",
                 base_url: str = "https://api.openai.com/v1", timeout: float = 60.0,
                 transport: Any | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def speak(self, text: str, *, voice: str = "alloy",
                    fmt: str = "mp3") -> bytes:
        if not self.enabled:
            raise MediaError("no speech provider configured (set OMNIFORGE_MEDIA_API_KEY)")
        if not text.strip():
            raise MediaError("text is required")
        payload = {"model": self.model, "input": text[:4000], "voice": voice,
                   "response_format": fmt}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        async with httpx.AsyncClient(**kwargs) as client:
            response = await client.post(f"{self.base_url}/audio/speech", json=payload,
                                         headers=headers)
        if response.status_code >= 400:
            raise MediaError(f"speech endpoint returned {response.status_code}: "
                             f"{response.text[:200]}")
        if not response.content:
            raise MediaError("speech endpoint returned no audio")
        return response.content


def status(settings) -> dict:
    return {
        "image_generation": bool(getattr(settings, "media_api_key", "")),
        "model": getattr(settings, "media_model", ""),
        "speech_generation": bool(getattr(settings, "media_api_key", "")),
        "video_generation": False,
        "music_generation": False,
        "model_3d_generation": False,
        "note": "only image generation has a provider wired; the rest is not implemented",
    }
