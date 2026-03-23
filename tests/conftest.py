import sys
import types

st = types.SimpleNamespace()
st.session_state = {}

def _cache_data(func=None, **kwargs):
    def decorator(f):
        return f
    return decorator(func) if func else decorator

st.cache_data = _cache_data
sys.modules['streamlit'] = st

supabase = types.SimpleNamespace(create_client=lambda *a, **k: None)
sys.modules['supabase'] = supabase
