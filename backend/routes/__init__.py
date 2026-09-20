"""
Routes package for Flask application
"""

from .library       import library_bp
from .analytics     import analytics_bp
from .settings      import settings_bp
from .system        import system_bp
from .genre         import genre_bp
from .notifications import notifications_bp
from .training      import training_bp
from .dj_coach      import dj_coach_bp

__all__ = [
    "library_bp", "analytics_bp", "settings_bp",
    "system_bp", "genre_bp", "notifications_bp", "training_bp", "dj_coach_bp",
]
