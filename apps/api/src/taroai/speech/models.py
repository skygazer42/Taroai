from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TranscriptionRequest(BaseModel):
    audio_base64: str = Field(min_length=1, max_length=14_000_000)
    content_type: Literal[
        "audio/mpeg", "audio/mp4", "audio/wav", "audio/webm", "audio/ogg"
    ]
    language: str | None = Field(default=None, max_length=20)
    model_config = ConfigDict(extra="forbid")


class SpeechSummaryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    max_characters: int = Field(default=1200, ge=100, le=10_000)
    model_config = ConfigDict(extra="forbid")


class TextToSpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    voice: str = Field(default="default", min_length=1, max_length=80)
    format: Literal["mp3", "wav", "opus"] = "mp3"
    model_config = ConfigDict(extra="forbid")
