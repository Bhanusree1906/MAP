"""Central logging setup for the agent process."""

import logging

# Third-party libraries whose wire-level traffic is only useful in debug mode.
_WIRE_LOGGERS = ("httpx", "httpcore", "mcp", "mcp.client", "langchain_mcp_adapters")


def configure_logging(level: str) -> bool:
    """Configure root logging plus LangChain's verbose/debug switches.

    Returns True if debug-level tracing is active (used by callers to decide
    whether to run the step-by-step streaming trace vs. a plain invoke).
    """
    numeric_level = getattr(logging, level, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    debug = numeric_level <= logging.DEBUG
    if debug:
        from langchain_core.globals import set_debug, set_verbose

        set_debug(True)
        set_verbose(True)
        for name in _WIRE_LOGGERS:
            logging.getLogger(name).setLevel(logging.DEBUG)
    else:
        for name in _WIRE_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)

    return debug
