import os
import asyncio
import mimetypes
from pydantic import BaseModel, Field
from google.antigravity import Agent
from google.antigravity.types import Document, Image, Audio, Video
from utils.agent_config import build_agent_config, model_timeout_seconds
from utils.document_formats import extract_document, read_text

class VerbatimItem(BaseModel):
    timestamp_start: str = Field(description="The timestamp of the dialogue or activity start (e.g. '00:00:15' or 'page 1')")
    timestamp_end: str = Field(description="The timestamp of the dialogue or activity end (e.g. '00:00:25' or 'page 1')")
    speaker: str = Field(description="Speaker name or label (e.g. 'Interviewer', 'Subject', 'Narrator', 'Visual Action')")
    content: str = Field(description="Verbatim spoken text or descriptive activity text (if video action)")

class ExtractedTranscript(BaseModel):
    filename: str = Field(description="The source filename")
    file_type: str = Field(description="The type of file (e.g. 'PDF Document', 'Audio Interview', 'Video Assessment')")
    summary: str = Field(description="A brief high-level overview of this session/file")
    items: list[VerbatimItem] = Field(description="The detailed chronological verbatim transcription/extraction items")

def load_media_file(filepath: str):
    """Loads a media file into the correct Antigravity media type."""
    ext = os.path.splitext(filepath)[1].lower()
    
    mime_type, _ = mimetypes.guess_type(filepath)
    if not mime_type:
        # Fallback based on extension
        if ext in ['.pdf', '.rtf', '.html', '.xml']:
            return Document.from_file(filepath)
        elif ext in ['.png', '.jpg', '.jpeg', '.webp']:
            return Image.from_file(filepath)
        elif ext in ['.mp3', '.wav', '.m4a', '.flac']:
            return Audio.from_file(filepath)
        elif ext in ['.mp4', '.webm', '.mov', '.avi']:
            return Video.from_file(filepath)
        raise ValueError(f"Unknown extension and MIME type for file {filepath}")
        
    if mime_type.startswith("image/"):
        return Image.from_file(filepath)
    elif mime_type.startswith("audio/"):
        return Audio.from_file(filepath)
    elif mime_type.startswith("video/"):
        return Video.from_file(filepath)
    elif mime_type == "application/pdf":
        return Document.from_file(filepath)
    elif mime_type.startswith("text/"):
        # Read as plain text to avoid unsupported Document MIME types
        return read_text(filepath)
    else:
        # Last resort: try reading as text
        try:
            return read_text(filepath)
        except (UnicodeDecodeError, ValueError):
            return Document.from_file(filepath)

async def transcribe_media(filepath: str, api_key: str = None, backend: str = None, gemini_model: str = None, ollama_model: str = None, ollama_base_url: str = None) -> dict:
    """Extract document text locally; reserve model transcription for audio/images/video."""
    filename = os.path.basename(filepath)
    sections = extract_document(filepath)
    if sections is not None:
        return ExtractedTranscript(
            filename=filename, file_type="Document", summary="",
            items=[VerbatimItem(timestamp_start=location, timestamp_end=location,
                                speaker="Document", content=text) for location, text in sections],
        ).model_dump()
    media = load_media_file(filepath)
    if isinstance(media, str):
        return ExtractedTranscript(
            filename=filename, file_type="Text Document", summary="",
            items=[VerbatimItem(timestamp_start="line 1", timestamp_end="end",
                                speaker="Document", content=media)],
        ).model_dump()

    system_instructions = (
        "You are an expert multimodal medical transcriber. Your task is to process input media files "
        "(audio, video, documents) and extract all spoken text, written text, and detailed visual descriptions "
        "(especially of subjects' physical/behavioral activities in video) verbatim.\n"
        "Ensure that for audio/video you capture timing indicators (e.g. '00:01:23'). "
        "For PDF/text documents, use page numbers or logical headers as timing/location indicators.\n"
        "Do not summarize or paraphrase dialogue - extract it word-for-word. "
        "For visual descriptions in video, log them chronologically as 'Visual Action' speaker items, "
        "describing the patient's behaviors, movements, or physical state in detail."
    )
    
    config = build_agent_config(
        system_instructions=system_instructions,
        response_schema=ExtractedTranscript,
        backend=backend,
        api_key=api_key,
        gemini_model=gemini_model,
        ollama_model=ollama_model,
        ollama_base_url=ollama_base_url,
    )
    
    prompt = (
        f"Verbatim extract and transcribe all content from this file '{filename}'.\n"
        "Provide verbatim dialogue and detailed behavioral activity descriptions where applicable."
    )
    
    timeout = model_timeout_seconds()
    try:
        async with asyncio.timeout(timeout), Agent(config=config) as agent:
            response = await agent.chat([prompt, media])
            data = await response.structured_output()
            return ExtractedTranscript.model_validate(data).model_dump()
    except TimeoutError:
        raise TimeoutError(f"Transcription of {filename} exceeded the {timeout:g}s model deadline. Try a smaller file or increase MODEL_TIMEOUT_SECONDS.") from None
