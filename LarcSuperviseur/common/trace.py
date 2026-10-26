import os

from larccommon.trace import disable, enable, set_trace_file, trace

# Même fichier qu'avant : LarcSuperviseur/trace.log (activé s'il existe).
set_trace_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'trace.log'))

__all__ = ["trace", "enable", "disable"]
