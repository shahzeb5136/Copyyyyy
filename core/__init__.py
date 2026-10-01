"""Core package for the Ask Your Data demo app.

The modules in this package are deliberately free of Streamlit imports (except
``config`` which can read ``st.secrets``) so they can be unit-tested and reused
outside the UI.
"""

__all__ = ["catalog", "charts", "config", "database", "llm", "pipeline", "prompts"]
