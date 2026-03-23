try:
    from supabase import create_client
except ImportError:
    create_client = None

def normalize_id(value):
    if value is None:
        return None
    return str(value).strip().lower()

def sanitize_user_input(value):
    if not value:
        return ""
    return str(value).strip()
