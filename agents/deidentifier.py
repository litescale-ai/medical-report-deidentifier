import json
from pydantic import BaseModel, Field
from utils.agent_config import generate_structured
from utils.hashing import generate_pseudonym_hash
from utils.identifier_rules import identifier_replacements, replace_data

class IdentifiedEntity(BaseModel):
    canonical_name: str = Field(description="The primary full name of the entity, e.g. 'John Doe' or 'Dr. Jane Smith'")
    entity_type: str = Field(description="The category of the entity, e.g., 'PATIENT', 'DOCTOR', 'RELATIVE', 'LOCATION', 'FACILITY'")
    relationship_context: str = Field(description="Role/relationship context, e.g., 'Subject of report', 'Mother of patient', 'Treating doctor'")
    variations: list[str] = Field(description="All exact variations of names, nicknames, initials, or titles found in the text (e.g. ['John Doe', 'John', 'Mr. Doe', 'J.D.'])")

class EntityDiscoveryResult(BaseModel):
    entities: list[IdentifiedEntity] = Field(description="List of all personal identifiable entities found in the text")

async def discover_pii_entities(chronology_data: dict, api_key: str = None, backend: str = None, gemini_model: str = None, ollama_model: str = None, ollama_base_url: str = None) -> list[dict]:
    """Discover PII entities, relationships, and aliases using validated model output."""
    
    system_instructions = (
        "You are an expert medical data privacy officer. Your task is to analyze chronological medical reports "
        "and discover every Personally Identifiable Information (PII) entity.\n"
        "You must:\n"
        "1. Identify every individual (patients, parents, doctors, relatives, therapists) and organizations/locations, including street, residential and postal addresses with street numbers, cities and postal codes.\n"
        "2. Specify their entity type ('PATIENT', 'DOCTOR', 'RELATIVE', 'LOCATION', 'FACILITY', or 'ORGANIZATION').\n"
        "3. Document their exact relationship role.\n"
        "4. Critical: List all variations/forms/aliases of their name that appear in the text (e.g., full name, first name, last name with title, initials) so they can be replaced deterministically."
    )
    
    prompt = (
        "Analyze the following medical report and identify all personal identifiable entities, "
        "their relationships, and name variations.\n\n"
        f"=== REPORT DATA ===\n{json.dumps(chronology_data, ensure_ascii=False, separators=(',', ':'))}"
    )
    
    data = await generate_structured(
        prompt, system_instructions=system_instructions, response_schema=EntityDiscoveryResult,
        backend=backend, api_key=api_key, gemini_model=gemini_model,
        ollama_model=ollama_model, ollama_base_url=ollama_base_url,
    )
    return data["entities"]

def perform_deidentification(chronology_data: dict, discovered_entities: list[dict], source_data=None) -> tuple[dict, dict, dict]:
    """Pseudonymise a report and remove rule-matched identifiers found in it and source_data.
    
    Returns:
        tuple containing:
        - The deidentified/pseudonymised report data dict
        - The secure identity catalogue mapping hash -> real details
        - The replacement map (real PII string -> pseudonym or removal marker)
    """
    rule_removals = identifier_replacements([source_data, chronology_data, discovered_entities])
    identity_catalogue = {}
    replacement_map = {} # real_variation -> hash
    
    generic_roles = {'patient', 'the patient', 'doctor', 'the doctor', 'dr', 'dr.', 'mr', 'mr.',
                     'mrs', 'mrs.', 'ms', 'ms.', 'clinician', 'relative', 'mother', 'father'}
    # 1. Generate stable hashes and register mappings
    for entity in discovered_entities:
        canon_name = entity["canonical_name"]
        entity_type = entity["entity_type"]
        rel_context = entity["relationship_context"]
        variations = [value for value in entity["variations"] if value.strip().casefold() not in generic_roles]
        
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

    # Local removal rules override model aliases and do not enter the reversible catalogue.
    replacement_map.update(rule_removals)
    deidentified_data = replace_data(chronology_data, replacement_map)
    # A model can include a telephone number or address in an entity name or relationship.
    # Keep matched identifiers out of both restored documents and the shareable relationship legend.
    identity_catalogue = replace_data(identity_catalogue, rule_removals)
    return deidentified_data, identity_catalogue, replacement_map
