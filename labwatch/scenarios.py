"""Failure scenarios and their ground truth, used for demos and evals.

Ground truth is for scoring only. Never show it to the agent.
"""

SCENARIOS = {
    "normal": {
        "inject": None,
        "truth": {"category": "none", "asset": None, "affected_rooms": [], "actions": []},
    },
    "ahu_failure": {
        "inject": ("ahu_failure", {"ahu": "AHU-4"}),
        "truth": {
            "category": "air_handler_failure",
            "asset": "AHU-4",
            "affected_rooms": ["412", "414", "418"],
            "actions": ["DOORS_CLOSED", "N95_ENTRY", "PREPARE_BACKUP_ROOM",
                        "LIMIT_ENTRY_PROTECTIVE", "INSPECT_AHU"],
        },
    },
    "sensor_fault": {
        "inject": ("sensor_fault", {"room": "412"}),
        "truth": {"category": "sensor_fault", "asset": "412",
                  "affected_rooms": ["412"], "actions": ["REPLACE_SENSOR"]},
    },
    "door_ajar": {
        "inject": ("door_ajar", {"room": "414"}),
        "truth": {"category": "door_not_latching", "asset": "414",
                  "affected_rooms": ["414"],
                  "actions": ["CHECK_DOOR_LATCH", "DOORS_CLOSED", "N95_ENTRY"]},
    },
}
