from pydantic import BaseModel, Field


class SpeechCapability(BaseModel):
    available: bool = False
    provider: str = "disabled"
    transcription: bool = False
    summarization: bool = False
    text_to_speech: bool = False
    max_audio_bytes: int = Field(default=10_000_000, ge=1)
    supported_audio_types: list[str] = Field(
        default_factory=lambda: [
            "audio/mpeg", "audio/mp4", "audio/wav", "audio/webm", "audio/ogg"
        ]
    )
    reason: str | None = "speech provider is not configured"


class SpeechGateway:
    def capabilities(self) -> SpeechCapability:
        return SpeechCapability()

    def transcribe(self, *, audio: bytes, content_type: str, language: str | None) -> str:
        raise RuntimeError("speech transcription provider is unavailable")

    def summarize(self, *, text: str, max_characters: int) -> str:
        raise RuntimeError("speech summarization provider is unavailable")

    def synthesize(self, *, text: str, voice: str, format: str) -> tuple[bytes, str]:
        raise RuntimeError("text-to-speech provider is unavailable")

