
import streamlit as st

def inject_design_system_css():
    st.markdown(
        '''
        <style>
          .ds-divider{height:1px;margin:18px 0 14px;background:linear-gradient(90deg, rgba(255,255,255,0.00), rgba(255,255,255,0.08), rgba(255,255,255,0.00));}
          .ds-section{margin: 0 0 10px 0;}
          .ds-section__title{font-size:1.12rem;font-weight:800;color:#F6F8FB;letter-spacing:-0.01em;margin-bottom:2px;}
          .ds-section__caption{font-size:.82rem;color:rgba(232,237,245,0.62);}
          div[data-testid="stExpander"]{border:1px solid rgba(255,255,255,.06);border-radius:12px;background:rgba(10,14,22,.35);}
        </style>
        ''',
        unsafe_allow_html=True,
    )
