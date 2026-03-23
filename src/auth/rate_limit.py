_STORE = {}

def _get_store():
    return _STORE

def _bucket(key):
    return _STORE.setdefault(key, [])
