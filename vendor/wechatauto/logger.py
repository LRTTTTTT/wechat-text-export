"""Silent adapter for the upstream database module's diagnostics.

Application diagnostics are emitted by our own logger without message content.
"""


class _SilentLogger:
    def debug(self, *args, **kwargs):
        pass

    info = debug
    warning = debug
    error = debug
    critical = debug


wxlog = _SilentLogger()
