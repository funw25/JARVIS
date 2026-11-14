"""Build explicit child environments for synthetic verification."""
import secrets


def isolated_environment(source, *, experimental_apswmc=False):
    """Remove inherited Jarvis configuration; opt in to a fresh experimental key."""
    result = {key: value for key, value in source.items() if not key.upper().startswith("JARVIS_")}
    if experimental_apswmc:
        result["JARVIS_STORAGE_ADAPTER"] = "apswmc"
        result["JARVIS_APSWMC_KEY"] = secrets.token_hex(32)
    return result
