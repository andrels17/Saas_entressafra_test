
import streamlit as st
from src.ui.components.sections import section_header, soft_divider

def admin_page_header(title: str, caption: str = ""):
    section_header(title, caption)

def admin_block(title: str, caption: str = ""):
    section_header(title, caption)

def admin_divider():
    soft_divider()
