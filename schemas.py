"""Tool schemas.

Descriptions are written for a model that has never heard of this plugin: they
say what the index *is*, when reaching for it beats asking the user, and what the
returned path can be used for. Three of the four tools are read-only; the fourth
indexes new files and never deletes anything.
"""

from __future__ import annotations

SCREEN_RECALL = {
    "name": "screen_recall",
    "description": (
        "Search the user's own screen history — every screenshot on this machine, indexed "
        "locally with OCR. Use it whenever the user refers to something they SAW rather than "
        "something they typed: 'that error I got yesterday', 'the pricing page', 'the message "
        "from Sam', 'what was on my screen when it broke'. Returns the matching screens with "
        "the time, the visible text around the match, and the image path. Prefer this over "
        "asking the user to re-send a screenshot."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Words that appeared on the screen, e.g. a product name, an error code, a title.",
            },
            "since": {
                "type": "string",
                "description": "Lower time bound: 'today', 'yesterday', '2h', '3d', '2026-11-04'. Empty means any time.",
            },
            "until": {
                "type": "string",
                "description": "Upper time bound, same forms as since. Usually empty.",
            },
            "limit": {"type": "integer", "description": "How many screens to return (default 8, max 25)."},
        },
        "required": ["query"],
    },
}

SCREEN_TIMELINE = {
    "name": "screen_timeline",
    "description": (
        "Show what was on the user's screen over a period, in order, interleaved with what the "
        "agent itself did. Use it to reconstruct a session ('what was I doing at 3pm?'), to find "
        "when something appeared ('when did I last see that dashboard?'), or to explain an agent "
        "action in the context of the screen it happened on. Defaults to today."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since": {"type": "string", "description": "'today' (default), 'yesterday', '4h', '2026-11-04'."},
            "until": {"type": "string", "description": "End of the window; empty means now."},
            "limit": {"type": "integer", "description": "Maximum entries (default 40, max 120)."},
        },
        "required": [],
    },
}

SCREEN_SHOW = {
    "name": "screen_show",
    "description": (
        "Open one screen from the index by id: its full OCR text, capture time and the path to "
        "the image. Use it after screen_recall when the snippet is not enough, or call it and "
        "then look at the returned path with a vision tool when the meaning is in the layout."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "The [#id] shown by screen_recall / screen_timeline."},
            "chars": {"type": "integer", "description": "How much of the text to return (default 6000)."},
        },
        "required": ["id"],
    },
}

SCREEN_INDEX = {
    "name": "screen_index",
    "description": (
        "Index new screenshots now instead of waiting for the background scan. Runs over the "
        "folders the user configured, or over explicit paths. Read-only with respect to the "
        "user's files: images are read, never moved or modified."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific image files or folders to index. Empty means all watched folders.",
            },
            "batch": {"type": "integer", "description": "Maximum new images to OCR in this call (default 40)."},
        },
        "required": [],
    },
}

ALL = (SCREEN_RECALL, SCREEN_TIMELINE, SCREEN_SHOW, SCREEN_INDEX)
