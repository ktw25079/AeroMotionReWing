from __future__ import annotations

"""Create R2 with the active-wing brake gate tied to the closed-loop brake."""

import hashlib
import json
import os
import shutil

from recurdyn import (
    IApplication,
    IExpression,
    IModelDocument,
    ISubSystem,
    dispatch_recurdyn,
)


HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE_ROOT = os.path.join(HERE, "R1_ACTIVE_WING_STABLE")
SOURCE_NAME = "Traxxas_TypeB_STAGE43_ACTIVE_WING_STABLE_AERO_66S.rdyn"
SOURCE_MODEL = os.path.join(SOURCE_ROOT, SOURCE_NAME)
OUTPUT_ROOT = os.path.join(HERE, "R2_ACTIVE_WING_CLOSED_LOOP")
OUTPUT_NAME = (
    "Traxxas_TypeB_STAGE43_R2_ACTIVE_WING_CLOSED_LOOP_AERO_66S.rdyn"
)
OUTPUT_MODEL = os.path.join(OUTPUT_ROOT, OUTPUT_NAME)
REPORT = os.path.join(
    OUTPUT_ROOT, "Reports", "stage43_r2_closed_loop_brake_gate.json"
)

EXPRESSION = "TRX27_EXPR_WING_BRAKE_COMMAND_FRACTION"
TEXT = "STEP5(EXPVAL(TRX28_EXPR_B_BRAKE_PCT),5,0,40,1)"
COMMENT = (
    "Stage43 R2 closed-loop brake gate. Uses the actual longitudinal "
    "controller brake percentage. The legacy RD_Driver VE_brake signal is "
    "not the active Stage42 closed-loop brake command and is intentionally "
    "excluded."
)


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def items(collection):
    try:
        return list(collection)
    except Exception:
        return [collection[index] for index in range(collection.Count)]


def by_name(collection):
    return {str(entity.Name): entity for entity in items(collection)}


def main():
    if not os.path.isfile(SOURCE_MODEL):
        raise FileNotFoundError(SOURCE_MODEL)
    if os.path.exists(OUTPUT_ROOT):
        raise FileExistsError(OUTPUT_ROOT)
    shutil.copytree(SOURCE_ROOT, OUTPUT_ROOT)
    copied_source = os.path.join(OUTPUT_ROOT, SOURCE_NAME)
    os.replace(copied_source, OUTPUT_MODEL)

    application = IApplication(
        dispatch_recurdyn(
            "2023", autoRunFlag=True, maxDelayTimeSec=240
        ).RecurDynApplication
    )
    document = IModelDocument(
        application.OpenModelDocument(OUTPUT_MODEL)
    )
    report = {
        "stage": "stage43_r2_closed_loop_brake_calibration",
        "source_model": SOURCE_MODEL,
        "source_sha256": sha256(SOURCE_MODEL),
        "output_model": OUTPUT_MODEL,
    }
    try:
        document.Activate()
        try:
            document.DeleteResults()
        except Exception:
            pass
        model = ISubSystem(document.Model)
        expressions = by_name(model.ExpressionCollection)
        if EXPRESSION not in expressions:
            raise KeyError(EXPRESSION)
        entity = IExpression(expressions[EXPRESSION])
        report["before"] = {
            "text": str(entity.Text),
            "comment": str(entity.Comment),
            "output": bool(entity.Output),
        }
        entity.Text = TEXT
        entity.Comment = COMMENT
        entity.Output = True
        report["after"] = {
            "text": str(entity.Text),
            "comment": str(entity.Comment),
            "output": bool(entity.Output),
        }
        report["validate_before_save"] = bool(document.Validate())
        if not report["validate_before_save"]:
            raise RuntimeError("R2 document validation failed")
        if not document.FileSave(OUTPUT_MODEL, True):
            raise RuntimeError("R2 model save failed")
    finally:
        application.CloseModelDocument(document)

    reopened = IModelDocument(
        application.OpenModelDocument(OUTPUT_MODEL)
    )
    try:
        reopened.Activate()
        model = ISubSystem(reopened.Model)
        entity = IExpression(
            by_name(model.ExpressionCollection)[EXPRESSION]
        )
        report["reopen"] = {
            "validate": bool(reopened.Validate()),
            "text": str(entity.Text),
            "comment": str(entity.Comment),
            "output": bool(entity.Output),
        }
    finally:
        application.CloseModelDocument(reopened)

    report["checks"] = {
        "source_unchanged": (
            sha256(SOURCE_MODEL) == report["source_sha256"]
        ),
        "document_valid": report["reopen"]["validate"],
        "closed_loop_brake_expression_persisted": (
            report["reopen"]["text"] == TEXT
        ),
        "legacy_driver_brake_removed": (
            "Ex_brake" not in report["reopen"]["text"]
        ),
    }
    report["output_sha256"] = sha256(OUTPUT_MODEL)
    report["output_bytes"] = os.path.getsize(OUTPUT_MODEL)
    report["passed"] = all(report["checks"].values())
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "output_model": OUTPUT_MODEL,
                "report": REPORT,
                "passed": report["passed"],
                "checks": report["checks"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not report["passed"]:
        raise RuntimeError("Stage43 R2 brake calibration failed")


if __name__ == "__main__":
    main()
