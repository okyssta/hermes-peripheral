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
        "Search the user's own screen history — screenshots on this machine plus any screens "
        "Peripheral captured itself, all indexed locally with OCR. Use it whenever the user refers "
        "to something they SAW rather than something they typed: 'that error I got yesterday', "
        "'the pricing page', 'the message from Sam', 'what was on my screen when it broke'. Returns "
        "the matching screens with the time, the visible text around the match, and the image path. "
        "Prefer this over asking the user to re-send a screenshot."
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
        "Open one screen from the index by id: its full OCR text, capture time and the stored "
        "image. Use it after screen_recall when the snippet is not enough. If the active model can "
        "see images, the screen itself is attached to the result — so a chart, a photo or a dialog "
        "whose meaning is in the layout is answerable even though the original is gone."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "The [#id] shown by screen_recall / screen_timeline."},
            "chars": {"type": "integer", "description": "How much of the text to return (default 6000)."},
            "image": {
                "type": "boolean",
                "description": "Attach the stored image. Omit to follow the model's declared vision support; set true only when you can see images.",
            },
        },
        "required": ["id"],
    },
}

SCREEN_CAPTURE = {
    "name": "screen_capture",
    "description": (
        "Capture the user's screen right now and remember it. Use when the user says 'look at my "
        "screen', 'what does this say', 'remember this', or asks about something currently on "
        "screen: the frame is read locally, indexed with its time, and — when the model supports "
        "vision — attached to the result so you can see it directly. No screenshot file, no "
        "clipboard, no user action required. Works on X11, Wayland, macOS and Windows; reports "
        "plainly when the machine has no display to capture."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": "Optional short label for why this screen matters, e.g. 'checkout error'.",
            },
            "image": {
                "type": "boolean",
                "description": "Attach the captured image. Omit to follow the model's declared vision support; set true only when you can see images.",
            },
        },
        "required": [],
    },
}

SCREEN_FORGET = {
    "name": "screen_forget",
    "description": (
        "Delete screens from the memory. Use it when the user says 'forget that', 'delete that "
        "screen', 'don't keep this' or asks for privacy cleanup. Removes the index row, the "
        "thumbnail, and the image file for screens Peripheral captured itself. The user's own "
        "screenshot files are never deleted. Pass an id for one screen, or all=true for everything."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "The [#id] to forget."},
            "all": {"type": "boolean", "description": "Forget every indexed screen."},
        },
        "required": [],
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

ALL = (SCREEN_RECALL, SCREEN_TIMELINE, SCREEN_SHOW, SCREEN_INDEX, SCREEN_CAPTURE, SCREEN_FORGET)
