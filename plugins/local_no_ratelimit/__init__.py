# -*- coding: utf-8 -*-
PLUGIN_NAME = "Local mirror: no rate limit"
PLUGIN_AUTHOR = "Hirokazu Kobayashi"
PLUGIN_DESCRIPTION = """
Removes Picard's built-in 1 request/second throttle when the configured
MusicBrainz server is a self-hosted mirror.

Picard throttles every host to 1 request/second by default (that limit exists
to respect the public musicbrainz.org server). Against your own local mirror it
is pointless and makes tagging painfully slow.

This plugin sets the minimum request delay to 0 for the server configured in
Options > General > MusicBrainz Server -- but ONLY when that server is NOT an
official MusicBrainz host, so it can never accidentally hammer musicbrainz.org.
If you change the server address, restart Picard.
"""
PLUGIN_VERSION = "1.1"
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

_done = False


def _remove_throttle():
    """Set the minimum request delay to 0 for the configured mirror host.
    Returns True once applied (or intentionally skipped for an official host).
    Never raises: config may not be ready at plugin-import time."""
    global _done
    if _done:
        return True
    try:
        host = config.setting["server_host"]
        port = config.setting["server_port"]
    except Exception:
        return False  # config not ready yet; try again later
    _done = True
    if host and host not in MUSICBRAINZ_SERVERS:
        hostkey = (host, port)
        ratecontrol.set_minimum_delay(hostkey, 0)
        ratecontrol.REQUEST_DELAY[hostkey] = 0
        log.info(
            "Local mirror: no rate limit -- throttle removed for %s:%s",
            host, port)
    else:
        log.info(
            "Local mirror: no rate limit -- server %r is official "
            "MusicBrainz; throttle left intact", host)
    return True


# Apply at import if config is already available; otherwise fall back to the
# first album load (config is always ready by then). Wrapped so the plugin
# always imports cleanly and appears in the plugin list.
if not _remove_throttle():
    def _apply_on_first_track(album, metadata, track, release):
        _remove_throttle()
    register_track_metadata_processor(_apply_on_first_track)
