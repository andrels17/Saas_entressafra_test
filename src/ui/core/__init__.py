"""Core UI utilities: design system, styles, sidebar, auth screens."""
from src.ui.core.design_system import inject_design_system_css
from src.ui.core.styles import inject_global_css, inject_mobile_css, NAV_CONFIG, page_header
from src.ui.core.sidebar_display import get_display_names, role_label
from src.ui.core.sidebar_counts import get_sidebar_badges, sidebar_badges

__all__ = [
    "inject_design_system_css",
    "inject_global_css",
    "inject_mobile_css",
    "NAV_CONFIG",
    "page_header",
    "get_display_names",
    "role_label",
    "get_sidebar_badges",
    "sidebar_badges",
]
