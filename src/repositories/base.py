import pandas as pd

def _mv_to_df(data):
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data)
