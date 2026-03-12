
import plotly.express as px
import pandas as pd

def horizontal_progress_bar(df: pd.DataFrame, x: str, y: str, text: str | None = None, height: int = 320):
    fig = px.bar(df, x=x, y=y, orientation="h", text=text or x)
    fig.update_traces(marker_color="#FFD100", texttemplate="%{text:.1f}%" if (text or x) == x else "%{text}", textposition="outside", cliponaxis=False)
    fig.update_layout(height=height, margin=dict(l=6, r=50, t=10, b=10), paper_bgcolor="#06080B", plot_bgcolor="#0C111A")
    return fig

def line_timeline(df: pd.DataFrame, x: str, y: str, height: int = 280):
    fig = px.line(df, x=x, y=y, markers=True)
    fig.update_layout(height=height, margin=dict(l=6, r=6, t=10, b=10), paper_bgcolor="#06080B", plot_bgcolor="#0C111A")
    return fig
