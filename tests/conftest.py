import sys
import types


class _CacheDecorator:
    def __call__(self, func=None, **kwargs):
        def decorator(f):
            return f
        wrapped = decorator(func) if func else decorator
        if not hasattr(wrapped, "clear"):
            wrapped.clear = lambda: None
        return wrapped


def _install_streamlit_stub():
    st = types.ModuleType("streamlit")
    st.session_state = {}
    st.secrets = {}
    st.cache_data = _CacheDecorator()
    st.cache_resource = _CacheDecorator()
    st.fragment = _CacheDecorator()
    st.column_config = types.SimpleNamespace(NumberColumn=lambda *a, **k: None)
    sys.modules["streamlit"] = st
    return st


def _install_supabase_stub():
    supabase = types.ModuleType("supabase")

    class Client:
        pass

    supabase.Client = Client
    supabase.create_client = lambda *a, **k: None
    sys.modules["supabase"] = supabase


_install_streamlit_stub()
_install_supabase_stub()


if "altair" not in sys.modules:
    altair = types.ModuleType("altair")
    sys.modules["altair"] = altair
