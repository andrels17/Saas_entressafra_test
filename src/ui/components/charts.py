
import plotly.express as px
import pandas as pd

from src.ui.core.plotly_theme import apply_dark_theme


def horizontal_progress_bar(
        df: pd.DataFrame,
        x: str,
        y: str,
        text: str | None = None,
        height: int = 320):
    fig = px.bar(df, x=x, y=y, orientation="h", text=text or x)
    fig.update_traces(
        marker_color="#FFD100",
        texttemplate="%{text:.1f}%" if (
            text or x) == x else "%{text}",
        textposition="outside",
        textfont=dict(color="#F5F5F5"),
        cliponaxis=False)
    apply_dark_theme(fig, height=height)
    return fig


def line_timeline(df: pd.DataFrame, x: str, y: str, height: int = 280):
    fig = px.line(df, x=x, y=y, markers=True)
    apply_dark_theme(fig, height=height)
    return fig
