import hashlib
import os

# Stable salt for deterministic pseudonymisation
# We generate a stable salt if it doesn't exist, and store it securely.
SALT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "secure", "salt.txt"
)

def get_or_create_salt() -> bytes:
    """Retrieves or creates a secure stable salt for hashing."""
    os.makedirs(os.path.dirname(SALT_FILE), exist_ok=True)
    if os.path.exists(SALT_FILE):
        with open(SALT_FILE, "r") as f:
            return f.read().strip().encode("utf-8")
    else:
        # Generate a stable salt
        salt = os.urandom(16).hex()
        with open(SALT_FILE, "w") as f:
            f.write(salt)
        return salt.encode("utf-8")

def generate_pseudonym_hash(entity_name: str, entity_type: str = "ENTITY") -> str:
    """Generates a stable, secure pseudonym hash for a given entity name.
    
    Args:
        entity_name: The raw PII name (e.g., 'John Doe').
        entity_type: The type of entity (e.g., 'PATIENT', 'DOCTOR', 'RELATIVE').
        
    Returns:
        A pseudonym string (e.g., 'PATIENT_a3b2c1d0').
    """
    salt = get_or_create_salt()
    # Normalize the name for consistency (case-insensitive, strip whitespace)
    normalized_name = entity_name.strip().lower()
    
    # Hash using SHA-256
    hasher = hashlib.sha256()
    hasher.update(salt)
    hasher.update(normalized_name.encode("utf-8"))
    
    # Use first 8 characters of hex digest for a clean, recognizable token
    hash_slice = hasher.hexdigest()[:8].upper()
    
    # Standard prefix based on entity type
    prefix = entity_type.strip().upper()
    return f"{prefix}_{hash_slice}"
