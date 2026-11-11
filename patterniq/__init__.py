"""PatternIQ: a pattern recogniser for manual trading."""

from .analysis import Analysis, AnalysisConfig, analyze
from .models import Detection, Level, Line, Pivot

__all__ = ["Analysis", "AnalysisConfig", "Detection", "Level", "Line", "Pivot", "analyze"]
__version__ = "0.1.0"
