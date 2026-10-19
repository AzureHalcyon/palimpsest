"""Event memory.

Layers:

    core/         settings, model client, text terms
    store/        state records, event graph, ingest
    read/         search, expansion, writer
    consolidate/  replay and edge formation
    chat/         tools and the turn loop
"""

__version__ = "0.1.0"
