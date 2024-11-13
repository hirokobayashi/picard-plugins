# -*- coding: utf-8 -*-

# Copyright (C) 2016 Anderson Mesquita <andersonvom@gmail.com>
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program. If not, see <http://www.gnu.org/licenses/>.

from picard import metadata
from picard.util.textencoding import unaccent

PLUGIN_NAME = "Non-ASCII Equivalents"
PLUGIN_AUTHOR = "Anderson Mesquita <andersonvom@trysometinghere>, Konrad Marciniak"
PLUGIN_VERSION = "0.5"
PLUGIN_API_VERSIONS = ["0.9", "0.10", "0.11", "0.15", "2.0"]
PLUGIN_LICENSE = "GPL-3.0-or-later"
PLUGIN_LICENSE_URL = "https://gnu.org/licenses/gpl.html"
PLUGIN_DESCRIPTION = '''Replaces accented and otherwise non-ASCII characters
with a somewhat equivalent version of their ASCII counterparts. This allows old
devices to be able to display song artists and titles somewhat correctly,
instead of displaying weird or blank symbols. It's an attempt to do a little
better than Musicbrainz's native "Replace non-ASCII characters" option.

Currently replaces characters on "album", "albumartist", "albumartists", "albumartistsort", "albumsort", "artist", "artists", "artistsort" and "title" tags.'''

CHAR_TABLE = {
    # Misc Letters
    "Å": "AA",
    "å": "aa",
    "Æ": "AE",
    "æ": "ae",
    "Œ": "OE",
    "œ": "oe",
    "ẞ": "ss",
    "ß": "ss",
    "Ø": "O",
    "ø": "o",
    "Ł": "L",
    "ł": "l",
    "Þ": "Th", # Thorn
    "þ": "th",
    "Ð": "D", # Eth
    "ð": "d",

    # Punctuation
    "¡": "!",
    "¿": "?",
    "–": "--",
    "—": "--",
    "―": "--",
    "«": "<<",
    "»": ">>",
    "‘": "'",
    "’": "'",
    "‚": ",",
    "‛": "'",
    "“": '"',
    "”": '"',
    "„": ",,",
    "‟": '"',
    "‹": "<",
    "›": ">",
    "⹂": ",,",
    "「": "|-",
    "」": "-|",
    "『": "|-",
    "』": "-|",
    "〝": '"',
    "〞": '"',
    "〟": ",,",
    "﹁": "-|",
    "﹂": "|-",
    "﹃": "-|",
    "﹄": "|-",
    "｢": "|-",
    "｣": "-|",
    "・": ".", # Katakana middle dot

    # Mathematics
    "≠": "!=",
    "≤": "<=",
    "≥": ">=",
    "±": "+-",
    "∓": "-+",
    "×": "x",
    "·": ".",
    "÷": "/",
    "√": "\\/",
    "∑": "E",
    "≪": "<<", # these are different
    "≫": ">>", # from the quotation marks

    # Misc
    "°": "o",
    "µ": "u",
    "ı": "i",
    "†": "t",
    "©": "(c)",
    "®": "(R)",
    "♥": "<3",
    "→": "-->",
    "☆": "*",
    "★": "*",
}

FILTER_TAGS = [
    "album",
    "albumartist",
    "albumartists",
    "albumartistsort",
    "albumsort",
    "artist",
    "artists",
    "artistsort",
    "title",
]


def sanitize(char):
    if char in CHAR_TABLE:
        return CHAR_TABLE[char]
    return unaccent(char)


def to_ascii(word):
    return "".join(sanitize(char) for char in word)


def main(tagger, metadata, *args):
    for name, value in metadata.rawitems():
        if name in FILTER_TAGS:
            metadata[name] = [to_ascii(x) for x in value]


metadata.register_track_metadata_processor(main)
metadata.register_album_metadata_processor(main)
