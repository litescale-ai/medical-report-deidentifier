import json
from pydantic import BaseModel, Field
from google.antigravity import Agent
from utils.agent_config import build_agent_config

class CatalogueEvent(BaseModel):
    timestamp: str = Field(description="Normalized timestamp or date/time of the event (e.g., '2026-05-27 10:00:00', 'Intake Day', or 'Session 1 - 00:05:12')")
    category: str = Field(description="The category of the event, e.g., 'Intake', 'Clinical Interview', 'Physical Assessment', 'Subject Activity'")
    source_file: str = Field(description="The source filename this event was extracted from")
    speaker: str = Field(description="The speaker or agent (e.g. 'Subject', 'Assessor', 'Visual Action')")
    event_details: str = Field(description="Verbatim dialogue text or detailed activity description")

class UnifiedChronology(BaseModel):
    patient_summary: str = Field(description="High-level synthesis of all sessions and documents analyzed")
    categories_found: list[str] = Field(description="List of categories identified across all sources")
    chronology: list[CatalogueEvent] = Field(description="The unified timeline of all events across all sessions, ordered chronologically")

async def catalogue_transcripts(transcripts: list[dict], api_key: str = None, backend: str = None, ollama_model: str = None) -> dict:
    """Uses the CataloguerAgent to compile multiple verbatim extractions into a unified chronological catalogue."""
    
    system_instructions = (
        "You are an expert medical data cataloguer. Your task is to ingest multiple verbatim transcriptions "
        "and document extractions, synthesize them, and organize them into a single, unified chronological timeline.\n"
        "You must: \n"
        "1. Identify the categories of interactions (e.g., 'Intake Form', 'Clinical Interview', 'Physical Assessment', 'Subject Activity').\n"
        "2. Interleave and sort all items strictly in chronological order by normalizing timestamps and timing headers.\n"
        "3. Preserve the exact verbatim details, dialogue, and activity descriptions from the inputs.\n"
        "4. Include references to the original source files for auditability."
    )
    
    config = build_agent_config(
        system_instructions=system_instructions,
        response_schema=UnifiedChronology,
        backend=backend,
        api_key=api_key,
        ollama_model=ollama_model,
    )
    
    # Format transcripts to feed into the prompt
    input_text = json.dumps(transcripts, indent=2)
    prompt = (
        "Organize the following verbatim extractions into a unified chronological medical report.\n"
        "Categorize each event, order them chronologically, and preserve all verbatim dialogue and visual descriptions.\n\n"
        f"=== TRANSCRIPTS DATA ===\n{input_text}"
    )
    
    async with Agent(config=config) as agent:
        response = await agent.chat(prompt)
        data = await response.structured_output()
        return data

