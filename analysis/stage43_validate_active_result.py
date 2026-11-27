from __future__ import annotations

"""Validate a fresh Stage43 active-wing RPLT and solver bundle."""

import hashlib
import json
import math
import os
from pathlib import Path

from recurdyn import IApplication, IPlotDocument, dispatch_recurdyn


RPLT = Path(os.environ["STAGE43_RPLT"]).resolve()
OUTPUT = Path(os.environ["STAGE43_VALIDATION_RESULT"]).resolve()
EXPECTED_DURATION_S = float(
    os.environ.get("STAGE43_EXPECTED_DURATION_S", "8")
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def unique(names: list[str], *needles: str) -> str:
    matches = [
        name
        for name in names
        if all(needle.casefold() in name.casefold() for needle in needles)
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one channel for {needles}; got {matches}"
        )
    return matches[0]


def values(plot: IPlotDocument, index: int, name: str) -> list[float]:
    return [
        float(value)
        for value in plot.GetPlotDataFromIndex(index, name)
    ]


def stats(data: list[float]) -> dict:
    return {
        "count": len(data),
        "min": min(data),
        "max": max(data),
        "mean": sum(data) / len(data),
        "first": data[0],
        "last": data[-1],
        "max_abs": max(abs(value) for value in data),
        "all_finite": all(math.isfinite(value) for value in data),
    }


def text_checks(result_dir: Path) -> dict:
    msg = result_dir / "TypeB_TEST.msg"
    log = result_dir / "TypeB_TEST.log"
    rmd = result_dir / "TypeB_TEST.rmd"
    message = msg.read_text(encoding="utf-8", errors="replace")
    log_text = log.read_text(encoding="utf-8", errors="replace")
    model_deck = rmd.read_text(encoding="utf-8", errors="replace")
    combined = message + "\n" + log_text
    forbidden = (
        "[C0249]",
        "The expression function (VX) is not available",
        "RecurDyn Solver has encountered a problem",
        "INTERRUPTED BY USER",
        "NaN",
        "Infinity",
    )
    return {
        "message_success": (
            "Analysis is accomplished successfully" in message
        ),
        "forbidden_tokens_absent": all(
            token.casefold() not in combined.casefold()
            for token in forbidden
        ),
        "forbidden_tokens": list(forbidden),
        "stable_aero_force_exported": (
            "RecurDyn_Data_Name='TRX25_FORCE_REAR_WING_AERO_ON_CHASSIS'"
            in model_deck
        ),
        "servo_force_exported": (
            "RecurDyn_Data_Name='TRX25_FORCE_REAR_WING_SPEED_SERVO'"
            in model_deck
        ),
        "regularizer_force_exported": (
            "RecurDyn_Data_Name='TRX25_FORCE_REAR_WING_STATIC_REGULARIZER'"
            in model_deck
        ),
        "unsafe_force_not_exported": (
            "RecurDyn_Data_Name='Translational1'" not in model_deck
        ),
        "invalid_motion_vx_absent": (
            "FUNCTION = STEP(VX(13)" not in model_deck
        ),
        "files": {
            "msg": str(msg),
            "log": str(log),
            "rmd": str(rmd),
        },
    }


def main() -> None:
    if not RPLT.is_file():
        raise FileNotFoundError(RPLT)
    result_dir = RPLT.parent
    application = IApplication(
        dispatch_recurdyn(
            "2023", autoRunFlag=True, maxDelayTimeSec=240
        ).RecurDynApplication
    )
    plot = IPlotDocument(
        application.NewPlotDocument("TRX43_ACTIVE_WING_VALIDATION")
    )
    try:
        index = int(plot.ImportPlotDataFile(str(RPLT)))
        names = list(plot.GetPlottableNameList(index))
        channels = {
            "time_s": unique(names, "TIME"),
            "speed_kph": unique(
                names, "TRX43_REQ_ACTIVE_WING_PROOF", "F1("
            ),
            "target_aoa_deg": unique(
                names, "TRX43_REQ_ACTIVE_WING_PROOF", "F2("
            ),
            "actual_aoa_deg": unique(
                names, "TRX43_REQ_ACTIVE_WING_PROOF", "F3("
            ),
            "tracking_error_deg": unique(
                names, "TRX43_REQ_ACTIVE_WING_PROOF", "F4("
            ),
            "drag_n": unique(
                names, "TRX43_REQ_ACTIVE_WING_PROOF", "F5("
            ),
            "downforce_n": unique(
                names, "TRX43_REQ_ACTIVE_WING_PROOF", "F6("
            ),
            "servo_torque_n_mm": unique(
                names, "TRX43_REQ_ACTIVE_WING_HEALTH", "F1("
            ),
            "actual_rate_deg_s": unique(
                names, "TRX43_REQ_ACTIVE_WING_HEALTH", "F2("
            ),
            "range_violation_deg": unique(
                names, "TRX43_REQ_ACTIVE_WING_HEALTH", "F3("
            ),
            "dynamic_pressure_pa": unique(
                names, "TRX43_REQ_ACTIVE_WING_HEALTH", "F4("
            ),
            "cd": unique(
                names, "TRX43_REQ_ACTIVE_WING_HEALTH", "F5("
            ),
            "cl": unique(
                names, "TRX43_REQ_ACTIVE_WING_HEALTH", "F6("
            ),
            "startup_fraction": unique(
                names, "TRX27_REQ_WING_MODE_MAP", "F1("
            ),
            "corner_fraction": unique(
                names, "TRX27_REQ_WING_MODE_MAP", "F2("
            ),
            "brake_fraction": unique(
                names, "TRX27_REQ_WING_MODE_MAP", "F3("
            ),
            "park_fraction": unique(
                names, "TRX27_REQ_WING_MODE_MAP", "F4("
            ),
            "driver_brake_raw": unique(
                names, "VariableEq/VE_brake", "VARVAL"
            ),
            "measured_ax_g": unique(
                names, "ExRq_chassis_acc", "F1("
            ),
            "brake_command_fraction": unique(
                names,
                "TRX27_EXPR_WING_BRAKE_COMMAND_FRACTION",
                "Value",
            ),
            "longitudinal_brake_pct": unique(
                names, "TRX28_EXPR_B_BRAKE_PCT", "Value"
            ),
        }
        data = {
            key: values(plot, index, name)
            for key, name in channels.items()
        }
    finally:
        application.ClosePlotDocument(plot)

    lengths = {key: len(value) for key, value in data.items()}
    row_count = len(data["time_s"])
    text = text_checks(result_dir)
    summaries = {
        key: stats(value)
        for key, value in data.items()
        if key != "time_s"
    }
    mode_occupancy = {
        name: {
            "fraction_over_0p5": (
                sum(value > 0.5 for value in data[name]) / row_count
            ),
            "fraction_over_0p9": (
                sum(value > 0.9 for value in data[name]) / row_count
            ),
        }
        for name in (
            "corner_fraction",
            "brake_fraction",
            "park_fraction",
        )
    }
    checks = {
        "all_channels_same_length": (
            len(set(lengths.values())) == 1 and row_count >= 2
        ),
        "time_starts_at_zero": math.isclose(
            data["time_s"][0], 0.0, abs_tol=1.0e-9
        ),
        "duration_matches": math.isclose(
            data["time_s"][-1],
            EXPECTED_DURATION_S,
            abs_tol=1.0e-6,
        ),
        "all_values_finite": all(
            all(math.isfinite(item) for item in values_)
            for values_ in data.values()
        ),
        "actual_aoa_inside_5_to_90_deg": (
            summaries["actual_aoa_deg"]["min"] >= 4.5
            and summaries["actual_aoa_deg"]["max"] <= 90.5
        ),
        "no_360deg_rotation": (
            summaries["actual_aoa_deg"]["max"]
            - summaries["actual_aoa_deg"]["min"] < 100.0
        ),
        "range_violation_below_0p5deg": (
            summaries["range_violation_deg"]["max_abs"] <= 0.5
        ),
        "servo_torque_within_limit": (
            summaries["servo_torque_n_mm"]["max_abs"] <= 3000.1
        ),
        # The settled vehicle can roll backwards by a few tenths of km/h for
        # a moment.  The signed drag expression correctly reverses with that
        # speed, so allow only the corresponding numerically tiny negative
        # displayed magnitude.
        "drag_direction_consistent_with_near_zero_rollback": (
            summaries["drag_n"]["min"] >= -1.0e-4
        ),
        "downforce_nonnegative": (
            summaries["downforce_n"]["min"] >= -1.0e-8
        ),
        "message_success": text["message_success"],
        "forbidden_solver_tokens_absent": text[
            "forbidden_tokens_absent"
        ],
        "stable_aero_force_exported": text[
            "stable_aero_force_exported"
        ],
        "servo_force_exported": text["servo_force_exported"],
        "regularizer_force_exported": text[
            "regularizer_force_exported"
        ],
        "unsafe_force_not_exported": text[
            "unsafe_force_not_exported"
        ],
        "invalid_motion_vx_absent": text[
            "invalid_motion_vx_absent"
        ],
    }
    result = {
        "rplt": str(RPLT),
        "rplt_sha256": sha256(RPLT),
        "rplt_bytes": RPLT.stat().st_size,
        "expected_duration_s": EXPECTED_DURATION_S,
        "row_count": row_count,
        "channel_names": channels,
        "channel_lengths": lengths,
        "stats": summaries,
        "mode_occupancy": mode_occupancy,
        "text_audit": text,
        "checks": checks,
        "passed": all(checks.values()),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "passed": result["passed"],
                "checks": checks,
                "key_stats": {
                    "speed_kph": summaries["speed_kph"],
                    "target_aoa_deg": summaries["target_aoa_deg"],
                    "actual_aoa_deg": summaries["actual_aoa_deg"],
                    "range_violation_deg": summaries[
                        "range_violation_deg"
                    ],
                    "servo_torque_n_mm": summaries[
                        "servo_torque_n_mm"
                    ],
                    "drag_n": summaries["drag_n"],
                    "downforce_n": summaries["downforce_n"],
                },
                "mode_occupancy": mode_occupancy,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not result["passed"]:
        raise RuntimeError("Stage43 active-wing validation failed")


if __name__ == "__main__":
    main()
