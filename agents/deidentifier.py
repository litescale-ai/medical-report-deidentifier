import json
from pydantic import BaseModel, Field
# pyrefly: ignore [missing-import]
from google.antigravity import Agent
from utils.agent_config import build_agent_config
from utils.hashing import generate_pseudonym_hash

class IdentifiedEntity(BaseModel):
    canonical_name: str = Field(description="The primary full name of the entity, e.g. 'John Doe' or 'Dr. Jane Smith'")
    entity_type: str = Field(description="The category of the entity, e.g., 'PATIENT', 'DOCTOR', 'RELATIVE', 'LOCATION', 'FACILITY'")
    relationship_context: str = Field(description="Role/relationship context, e.g., 'Subject of report', 'Mother of patient', 'Treating doctor'")
    variations: list[str] = Field(description="All exact variations of names, nicknames, initials, or titles found in the text (e.g. ['John Doe', 'John', 'Mr. Doe', 'J.D.'])")

class EntityDiscoveryResult(BaseModel):
    entities: list[IdentifiedEntity] = Field(description="List of all personal identifiable entities found in the text")

async def discover_pii_entities(chronology_data: dict, api_key: str = None, backend: str = None, gemini_model: str = None, ollama_model: str = None) -> list[dict]:
    """Uses DeidentifierAgent to discover all PII entities, relationships, and name variations."""
    
    system_instructions = (
        "You are an expert medical data privacy officer. Your task is to analyze chronological medical reports "
        "and discover every Personally Identifiable Information (PII) entity.\n"
        "You must:\n"
        "1. Identify every individual (patients, parents, doctors, relatives, therapists) and organizations/locations.\n"
        "2. Specify their entity type ('PATIENT', 'DOCTOR', 'RELATIVE', 'LOCATION', 'FACILITY', or 'ORGANIZATION').\n"
        "3. Document their exact relationship role.\n"
        "4. Critical: List all variations/forms/aliases of their name that appear in the text (e.g., full name, first name, last name with title, initials) so they can be replaced deterministically."
    )
    
    config = build_agent_config(
        system_instructions=system_instructions,
        response_schema=EntityDiscoveryResult,
        backend=backend,
        api_key=api_key,
        gemini_model=gemini_model,
        ollama_model=ollama_model,
    )
    
    prompt = (
        "Analyze the following medical report and identify all personal identifiable entities, "
        "their relationships, and name variations.\n\n"
        f"=== REPORT DATA ===\n{json.dumps(chronology_data, indent=2)}"
    )
    
    async with Agent(config=config) as agent:
        response = await agent.chat(prompt)
        data = await response.structured_output()
        return data.get("entities", [])

def perform_deidentification(chronology_data: dict, discovered_entities: list[dict]) -> tuple[dict, dict, dict]:
    """Deterministically pseudonymises the chronological report.
    
    Returns:
        tuple containing:
        - The deidentified/pseudonymised report data dict
        - The secure identity catalogue mapping hash -> real details
        - The replacement map (real PII string -> pseudonym hash), sorted by key length descending
    """
    identity_catalogue = {}
    replacement_map = {} # real_variation -> hash
    
    # 1. Generate stable hashes and register mappings
    for entity in discovered_entities:
        canon_name = entity["canonical_name"]
        entity_type = entity["entity_type"]
        rel_context = entity["relationship_context"]
        variations = entity["variations"]
        
        # Generate the hash pseudonym
        pseudonym_hash = generate_pseudonym_hash(canon_name, entity_type)
        
        # Build the Identity Catalogue entry
        identity_catalogue[pseudonym_hash] = {
            "canonical_name": canon_name,
            "entity_type": entity_type,
            "relationship_context": rel_context,
            "variations": variations
        }
        
        # Build the replacement map
        # Make sure variations are clean
        for var in variations:
            var_stripped = var.strip()
            if var_stripped:
                replacement_map[var_stripped] = pseudonym_hash
        # Ensure canonical name is also mapped
        replacement_map[canon_name.strip()] = pseudonym_hash

    # 2. Sort replacement variations by length descending to prevent partial matches 
    # (e.g., replacing 'Dr. John Smith' before 'John Smith' or 'John')
    sorted_replacements = sorted(replacement_map.keys(), key=len, reverse=True)
    
    # Convert chronology_data to a JSON string, perform replacements, and load it back
    chrono_str = json.dumps(chronology_data, ensure_ascii=False)
    
    for real_str in sorted_replacements:
        if not real_str:
            continue
        # We replace the exact string case-sensitively or case-insensitively if needed,
        # but since variations were extracted exactly, case-sensitive replace is safer.
        pseudonym = replacement_map[real_str]
        chrono_str = chrono_str.replace(real_str, pseudonym)
        
    deidentified_data = json.loads(chrono_str)
    return deidentified_data, identity_catalogue, replacement_map
