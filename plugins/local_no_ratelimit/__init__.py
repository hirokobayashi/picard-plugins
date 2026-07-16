# -*- coding: utf-8 -*-
PLUGIN_NAME = "Local mirror: tunable rate limit"
PLUGIN_AUTHOR = "Hirokazu Kobayashi"
PLUGIN_DESCRIPTION = """
Overrides Picard's built-in 1 request/second throttle when the configured
MusicBrainz server is a self-hosted mirror, with a delay you can tune.

Picard throttles every host to 1 request/second by default (that limit exists
to respect the public musicbrainz.org server). Against your own mirror it is
pointlessly slow -- but 0 delay can overload a small mirror, causing request
timeouts and "exhausted max retries" errors (which are slower and lossy).

Set the minimum delay per request in Options > Advanced > Local mirror rate
limit. The override applies ONLY when the configured server is NOT an official
MusicBrainz host, so it can never affect musicbrainz.org. Delay changes take
effect immediately on save; a server-address change needs a Picard restart.
"""
PLUGIN_VERSION = "2.0"
PLUGIN_API_VERSIONS = [
    "2.0", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6",
    "2.7", "2.8", "2.9", "2.10", "2.11", "2.12", "2.13",
]
PLUGIN_LICENSE = "GPL-2.0-or-later"
PLUGIN_LICENSE_URL = "https://www.gnu.org/licenses/gpl-2.0.html"

from picard import config, log
from picard.const import MUSICBRAINZ_SERVERS
from picard.metadata import register_track_metadata_processor
from picard.webservice import ratecontrol

DELAY_OPTION = "local_ratelimit_delay_ms"
DEFAULT_DELAY_MS = 100   # ~10 requests/second; safe starting point for a mirror


def _delay_ms():
    try:
        return max(0, int(config.setting[DELAY_OPTION]))
    except Exception:
        return DEFAULT_DELAY_MS


def apply_delay():
    """Set the request delay for the configured mirror host. Never raises
    (config may be unavailable at plugin-import time). Returns True once the
    server host is known (applied, or skipped for an official host)."""
    try:
        host = config.setting["server_host"]
        port = config.setting["server_port"]
    except Exception:
        return False
    if host and host not in MUSICBRAINZ_SERVERS:
        delay = _delay_ms()
        hostkey = (host, port)
        ratecontrol.set_minimum_delay(hostkey, delay)
        ratecontrol.REQUEST_DELAY[hostkey] = delay
        log.info(
            "Local mirror rate limit: %s ms/request for %s:%s",
            delay, host, port)
    else:
        log.info(
            "Local mirror rate limit: server %r is official MusicBrainz; "
            "throttle left intact", host)
    return True


# Apply at import if config is ready; otherwise on the first album load.
if not apply_delay():
    def _apply_on_first_track(album, metadata, track, release):
        apply_delay()
    register_track_metadata_processor(_apply_on_first_track)


# ---- Options page (Options > Advanced > Local mirror rate limit) -----------
# Wrapped so a UI-less context (e.g. tests) still loads the plugin cleanly.
try:
    from PyQt5 import QtWidgets
    from picard.config import IntOption
    from picard.ui.options import OptionsPage, register_options_page

    class LocalRateLimitOptionsPage(OptionsPage):
        NAME = "local_ratelimit"
        TITLE = "Local mirror rate limit"
        PARENT = "advanced"
        options = [IntOption("setting", DELAY_OPTION, DEFAULT_DELAY_MS)]

        def __init__(self, parent=None):
            super().__init__(parent)
            layout = QtWidgets.QVBoxLayout(self)
            info = QtWidgets.QLabel(
                "Minimum delay between requests to a self-hosted MusicBrainz "
                "mirror (the server set in Options > General).\n\n"
                "0 = no limit (fastest, but can overload a small mirror and "
                "cause timeouts / 'exhausted max retries').\n"
                "100 ms ≈ 10 req/s, 200 ms ≈ 5 req/s. Increase if you "
                "see retry/timeout errors; decrease if the server keeps up.\n\n"
                "Only applies when the server is not an official MusicBrainz "
                "host.")
            info.setWordWrap(True)
            layout.addWidget(info)
            row = QtWidgets.QHBoxLayout()
            row.addWidget(QtWidgets.QLabel("Minimum delay per request:"))
            self.spin = QtWidgets.QSpinBox()
            self.spin.setRange(0, 10000)
            self.spin.setSingleStep(10)
            self.spin.setSuffix(" ms")
            row.addWidget(self.spin)
            row.addStretch(1)
            layout.addLayout(row)
            layout.addStretch(1)

        def load(self):
            self.spin.setValue(_delay_ms())

        def save(self):
            config.setting[DELAY_OPTION] = self.spin.value()
            apply_delay()   # take effect immediately, no restart needed

    register_options_page(LocalRateLimitOptionsPage)
except Exception as e:  # pragma: no cover - UI not available
    log.debug("Local mirror rate limit: options page not registered (%r)", e)
