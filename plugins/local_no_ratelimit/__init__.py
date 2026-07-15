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
PLUGIN_VERSION = "1.0"
PLUGIN_API_VERSIONS = [
    "2.0", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6",
    "2.7", "2.8", "2.9", "2.10", "2.11", "2.12", "2.13",
]
PLUGIN_LICENSE = "GPL-2.0-or-later"
PLUGIN_LICENSE_URL = "https://www.gnu.org/licenses/gpl-2.0.html"

from picard import config, log
from picard.const import MUSICBRAINZ_SERVERS
from picard.webservice import ratecontrol

_host = config.setting["server_host"]
_port = config.setting["server_port"]

if _host and _host not in MUSICBRAINZ_SERVERS:
    _hostkey = (_host, _port)
    # Minimum enforced delay -> 0, and reset the current adaptive delay so it
    # takes effect from the very first request rather than converging down.
    ratecontrol.set_minimum_delay(_hostkey, 0)
    ratecontrol.REQUEST_DELAY[_hostkey] = 0
    log.info(
        "Local mirror: no rate limit -- throttle removed for %s:%s",
        _host, _port)
else:
    log.info(
        "Local mirror: no rate limit -- server %r is official MusicBrainz; "
        "throttle left intact", _host)
