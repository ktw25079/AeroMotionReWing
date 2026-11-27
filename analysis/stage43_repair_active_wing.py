from __future__ import annotations

"""Repair the user-modified Stage42J model into a stable active-wing model.

The input model is treated as immutable.  A clean portable bundle is created
and the following changes are made through the RecurDyn 2023 ProcessNet API:

* disable the user-added force between the moving wing and a remote chassis
  marker;
* remove the invalid state-dependent prescribed hinge Motion;
* restore the validated torque-servo plus rotational regularizer;
* apply the aerodynamic resultant to the chassis equivalent pressure centre
  in the chassis driver frame;
* restore the bounded speed/corner/brake/park angle map; and
* add proof-oriented output requests.
"""

import hashlib
import json
import math
import os
import shutil
import traceback

from recurdyn import (
    ForceDisplay,
    IApplication,
    IBody,
    IExpression,
    IForceAxialRotational,
    IForceSpringRotational,
    IForceTranslational,
    IJointRevolute,
    IModelDocument,
    IRequestExpression,
    ISubSystem,
    dispatch_recurdyn,
)


HERE = os.path.dirname(os.path.abspath(__file__))
INPUT_ROOT = os.path.join(HERE, "R1_INPUT")
INPUT_MODEL = os.path.join(
    INPUT_ROOT,
    "Traxxas_TypeB_STAGE42J_CLOSED_LOOP_FINAL_FIXED_WING_SEAMFIX_66S.rdyn",
)
OUTPUT_ROOT = os.path.join(HERE, "R1_ACTIVE_WING_STABLE")
OUTPUT_MODEL = os.path.join(
    OUTPUT_ROOT,
    "Traxxas_TypeB_STAGE43_ACTIVE_WING_STABLE_AERO_66S.rdyn",
)
REPORT = os.path.join(OUTPUT_ROOT, "Reports", "stage43_active_wing_repair.json")

WING_JOINT = "TRX25_JOINT_REAR_WING_HINGE"
WING_SERVO = "TRX25_FORCE_REAR_WING_SPEED_SERVO"
WING_REGULARIZER = "TRX25_FORCE_REAR_WING_STATIC_REGULARIZER"
WING_AERO = "TRX25_FORCE_REAR_WING_AERO_ON_CHASSIS"
UNSAFE_FORCE = "Translational1"

WING_AREA_M2 = 0.05244
WING_ASPECT_RATIO = 2.75
AIR_DENSITY_KG_M3 = 1.225
OSWALD_EFFICIENCY = 0.8
HINGE_Q_MIN_RAD = 0.0
HINGE_Q_MAX_RAD = math.radians(85.0)
INITIAL_Q_RAD = math.radians(10.0)
SERVO_TORQUE_LIMIT_N_MM = 3000.0
SERVO_VELOCITY_GAIN_N_MM_S_PER_RAD = 1800.0
REGULARIZER_STIFFNESS_N_MM_PER_RAD = 100.0

WING_REQUESTS = (
    "TRX25_REQ_ACTIVE_WING_CONTROL",
    "TRX25_REQ_ACTIVE_WING_AERO",
    "TRX27_REQ_WING_MODE_MAP",
    "TRX28_REQ_RC48_AERO_SANITY",
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


def prepare_output_bundle() -> dict:
    if not os.path.isfile(INPUT_MODEL):
        raise FileNotFoundError(INPUT_MODEL)
    if os.path.exists(OUTPUT_ROOT):
        raise FileExistsError(
            f"Output already exists; refusing to overwrite: {OUTPUT_ROOT}"
        )

    os.makedirs(os.path.join(OUTPUT_ROOT, "Reports"))
    shutil.copy2(INPUT_MODEL, OUTPUT_MODEL)
    copied = [os.path.basename(OUTPUT_MODEL)]
    for name in ("path.mat", "RD_Driver.dll", "PORTABLE_MANIFEST.json"):
        source = os.path.join(INPUT_ROOT, name)
        if os.path.isfile(source):
            shutil.copy2(source, os.path.join(OUTPUT_ROOT, name))
            copied.append(name)
    for name in ("Road_Files", "Tire_Files"):
        source = os.path.join(INPUT_ROOT, name)
        if not os.path.isdir(source):
            raise FileNotFoundError(source)
        shutil.copytree(source, os.path.join(OUTPUT_ROOT, name))
        copied.append(name + "/")
    return {"copied": copied, "source_sha256": sha256(INPUT_MODEL)}


def expression_state(entity) -> dict:
    wrapped = IExpression(entity)
    return {
        "text": str(wrapped.Text),
        "output": bool(wrapped.Output),
        "comment": str(wrapped.Comment),
    }


def set_expression(expressions, name: str, text: str, comment: str):
    if name not in expressions:
        raise KeyError(f"Required existing expression is missing: {name}")
    entity = IExpression(expressions[name])
    before = expression_state(entity)
    entity.Text = text
    entity.Output = True
    entity.Comment = comment
    return entity, before


def set_or_create_expression(model, expressions, name, text, comment):
    if name in expressions:
        entity = IExpression(expressions[name])
        before = expression_state(entity)
    else:
        entity = IExpression(model.CreateExpression(name, text))
        expressions[name] = entity
        before = None
    entity.Text = text
    entity.Output = True
    entity.Comment = comment
    return entity, before


def configure_expressions(model) -> dict:
    expressions = by_name(model.ExpressionCollection)
    changed = {}

    formulas = {
        "TRX35_EXPR_FIXED_WING_Q_RAD": (
            "0",
            "Stage43 compatibility placeholder only; the active hinge does not use prescribed Motion.",
        ),
        "TRX27_EXPR_WING_STARTUP_VALID_FRACTION": (
            "STEP5(TIME,0.05,0,0.25,1)",
            "Rejects the uninitialised t=0 measured-state sample.",
        ),
        "TRX27_EXPR_WING_CORNER_SPEED_GATE": (
            "STEP5(ABS(EXPVAL(Ex_mea_chassis_vx)),6.324555320337,0,12.649110640674,1)",
            "RC-scale corner-aero speed gate (Froude-equivalent 20 to 40 km/h full scale).",
        ),
        "TRX27_EXPR_WING_HIGH_SPEED_STRAIGHT_FRACTION": (
            "EXPVAL(TRX28_EXPR_WING_OPERATION_ENABLE)*"
            "STEP5(ABS(EXPVAL(Ex_mea_chassis_vx)),12.649110640674,0,25.298221281347,1)",
            "RC-scale low-drag gate (Froude-equivalent 40 to 80 km/h full scale).",
        ),
        "TRX27_EXPR_WING_CORNER_FRACTION": (
            "EXPVAL(TRX28_EXPR_WING_OPERATION_ENABLE)*"
            "EXPVAL(TRX27_EXPR_WING_CORNER_SPEED_GATE)*"
            "MAX(STEP5(ABS(EXPVAL(Ex_mea_steer_wheel_angle_deg)),10,0,45,1),"
            "MAX(STEP5(ABS(EXPVAL(Ex_mea_chassis_yaw_rate)),5,0,20,1),"
            "STEP5(ABS(EXPVAL(Ex_mea_chassis_accy)),0.12,0,0.55,1)))",
            "Corner demand is the maximum of steering, yaw-rate and lateral-acceleration gates.",
        ),
        "TRX27_EXPR_WING_BRAKE_COMMAND_FRACTION": (
            "STEP5(EXPVAL(TRX28_EXPR_B_BRAKE_PCT),5,0,40,1)",
            "Closed-loop longitudinal-controller brake-command air-brake gate; the legacy driver DLL VE_brake signal is not used.",
        ),
        "TRX27_EXPR_WING_STRONG_DECEL_FRACTION": (
            "STEP5(-EXPVAL(Ex_mea_chassis_accx),0.45,0,0.8,1)",
            "Strong longitudinal-deceleration fallback gate.",
        ),
        "TRX27_EXPR_WING_BRAKE_FRACTION": (
            "EXPVAL(TRX28_EXPR_WING_OPERATION_ENABLE)*MAX("
            "EXPVAL(TRX28_EXPR_WING_BRAKE_SPEED_GATE)*MAX("
            "EXPVAL(TRX27_EXPR_WING_BRAKE_COMMAND_FRACTION),"
            "EXPVAL(TRX28_EXPR_WING_TERMINAL_PHASE_ENABLE)*"
            "EXPVAL(TRX27_EXPR_WING_STRONG_DECEL_FRACTION)),"
            "EXPVAL(TRX28_EXPR_WING_TERMINAL_PHASE_ENABLE)*"
            "STEP5(ABS(EXPVAL(Ex_mea_chassis_vx)),5,1,7,0))",
            "Validated terminal air-brake hold and commanded/deceleration brake priority.",
        ),
        "TRX27_EXPR_WING_PARK_FRACTION": (
            "EXPVAL(TRX28_EXPR_WING_OPERATION_ENABLE)*"
            "EXPVAL(TRX28_EXPR_WING_TERMINAL_PHASE_ENABLE)*"
            "STEP5(ABS(EXPVAL(Ex_mea_chassis_vx)),2,1,5,0)",
            "Terminal park blend: 85 deg hinge q (90 deg physical incidence) below 2 km/h.",
        ),
        "TRX27_EXPR_WING_STRAIGHT_TARGET_Q_RAD": (
            "0.1745329251994329+"
            "((0.08726646259971647+(0-0.08726646259971647)*"
            "EXPVAL(TRX27_EXPR_WING_HIGH_SPEED_STRAIGHT_FRACTION))"
            "-0.1745329251994329)*EXPVAL(TRX28_EXPR_WING_OPERATION_ENABLE)",
            "Safe launch q=10 deg, normal straight q=5 deg and high-speed q=0 deg.",
        ),
        "TRX27_EXPR_WING_BASE_TARGET_Q_RAD": (
            "EXPVAL(TRX27_EXPR_WING_STRAIGHT_TARGET_Q_RAD)+"
            "(0.6108652381980153-EXPVAL(TRX27_EXPR_WING_STRAIGHT_TARGET_Q_RAD))*"
            "EXPVAL(TRX27_EXPR_WING_CORNER_FRACTION)",
            "Straight target blended to q=35 deg in full corner mode.",
        ),
        "TRX27_EXPR_WING_BRAKE_TARGET_Q_RAD": (
            "EXPVAL(TRX27_EXPR_WING_BASE_TARGET_Q_RAD)+"
            "(1.396263401595464-EXPVAL(TRX27_EXPR_WING_BASE_TARGET_Q_RAD))*"
            "EXPVAL(TRX27_EXPR_WING_BRAKE_FRACTION)",
            "Base target blended to q=80 deg during braking.",
        ),
        "TRX25_EXPR_WING_TARGET_Q_RAD": (
            "MAX(0,MIN(1.48352986419518,"
            "EXPVAL(TRX27_EXPR_WING_BRAKE_TARGET_Q_RAD)+"
            "(1.48352986419518-EXPVAL(TRX27_EXPR_WING_BRAKE_TARGET_Q_RAD))*"
            "EXPVAL(TRX27_EXPR_WING_PARK_FRACTION)))",
            "Hard-bounded target q in [0,85] deg; physical incidence is [5,90] deg.",
        ),
        "TRX25_EXPR_WING_LOW_DRAG_FRACTION": (
            "EXPVAL(TRX27_EXPR_WING_HIGH_SPEED_STRAIGHT_FRACTION)*"
            "MAX(0,MIN(1,1-MAX(EXPVAL(TRX27_EXPR_WING_CORNER_FRACTION),"
            "MAX(EXPVAL(TRX27_EXPR_WING_BRAKE_FRACTION),"
            "EXPVAL(TRX27_EXPR_WING_PARK_FRACTION)))))",
            "Low-drag mode after corner, brake and park priorities.",
        ),
        "TRX25_EXPR_WING_AIRBRAKE_FRACTION": (
            "MAX(EXPVAL(TRX27_EXPR_WING_BRAKE_FRACTION),"
            "EXPVAL(TRX27_EXPR_WING_PARK_FRACTION))",
            "Combined brake/park high-angle fraction.",
        ),
        "TRX27_EXPR_WING_MAX_DEPLOY_RATE_RAD_S": (
            "1.396263401595464+0.2617993877991494*"
            "EXPVAL(TRX25_EXPR_WING_AIRBRAKE_FRACTION)",
            "Deploy rate limit: 80 deg/s corner, 95 deg/s brake/park.",
        ),
        "TRX25_EXPR_WING_DESIRED_RATE_RAD_S": (
            "MAX(-0.6981317007977318,"
            "MIN(EXPVAL(TRX27_EXPR_WING_MAX_DEPLOY_RATE_RAD_S),"
            "12*(EXPVAL(TRX25_EXPR_WING_TARGET_Q_RAD)-"
            "EXPVAL(TRX25_EXPR_WING_ACTUAL_Q_RAD))))",
            "Rate-limited target tracking; retract limit is 40 deg/s.",
        ),
        "TRX25_EXPR_WING_SERVO_TORQUE_N_MM": (
            "MAX(-3000,MIN(3000,1800*"
            "EXPVAL(TRX25_EXPR_WING_DESIRED_RATE_RAD_S)))",
            "Validated saturated velocity-servo command; the paired damper supplies actual-rate feedback.",
        ),
        "TRX27_EXPR_WING_TARGET_AOA_DEG": (
            "5+EXPVAL(TRX25_EXPR_WING_TARGET_Q_RAD)*180/PI",
            "Target physical aerodynamic incidence in degrees.",
        ),
        "TRX27_EXPR_WING_ACTUAL_AOA_DEG": (
            "5+EXPVAL(TRX25_EXPR_WING_ACTUAL_Q_RAD)*180/PI",
            "Actual physical aerodynamic incidence in degrees.",
        ),
        "TRX28_EXPR_WING_ALPHA_RAD": (
            "MAX(0,MIN(PI/2,EXPVAL(TRX27_EXPR_WING_ACTUAL_AOA_DEG)*PI/180))",
            "Actual aerodynamic incidence limited to the intended 0 to 90 deg coefficient domain.",
        ),
        "TRX25_EXPR_WING_CL_DOWN": (
            "MAX(0,0.65*SIN(2*EXPVAL(TRX28_EXPR_WING_ALPHA_RAD)))",
            "Engineering-assumption downforce coefficient from actual wing angle.",
        ),
        "TRX25_EXPR_WING_CD": (
            "0.12+SIN(EXPVAL(TRX28_EXPR_WING_ALPHA_RAD))*"
            "SIN(EXPVAL(TRX28_EXPR_WING_ALPHA_RAD))+"
            "EXPVAL(TRX25_EXPR_WING_CL_DOWN)*EXPVAL(TRX25_EXPR_WING_CL_DOWN)/"
            f"(PI*{OSWALD_EFFICIENCY:.16g}*{WING_ASPECT_RATIO:.16g})",
            "Drag coefficient using CAD aspect ratio 2.75 and Oswald efficiency 0.8.",
        ),
        "TRX28_EXPR_WING_DYNAMIC_PRESSURE_PA": (
            f"0.5*{AIR_DENSITY_KG_M3:.16g}*"
            "(EXPVAL(Ex_mea_chassis_vx)/3.6)*"
            "(EXPVAL(Ex_mea_chassis_vx)/3.6)",
            "Dynamic pressure from chassis-local longitudinal speed.",
        ),
        "TRX25_EXPR_WING_AERO_FX_N": (
            f"-0.5*{AIR_DENSITY_KG_M3:.16g}*{WING_AREA_M2:.16g}*"
            "EXPVAL(TRX25_EXPR_WING_CD)*"
            "(EXPVAL(Ex_mea_chassis_vx)/3.6)*"
            "ABS(EXPVAL(Ex_mea_chassis_vx)/3.6)",
            "Chassis-local rear-wing drag resultant, N, using CAD planform area 0.05244 m^2.",
        ),
        "TRX25_EXPR_WING_AERO_FY_N": (
            "0",
            "No lateral component in the equivalent rear-wing load model.",
        ),
        "TRX25_EXPR_WING_AERO_FZ_N": (
            f"-EXPVAL(TRX28_EXPR_WING_DYNAMIC_PRESSURE_PA)*{WING_AREA_M2:.16g}*"
            "EXPVAL(TRX25_EXPR_WING_CL_DOWN)",
            "Chassis-local rear-wing downforce resultant, N.",
        ),
    }

    for name, (text, comment) in formulas.items():
        entity, before = set_expression(expressions, name, text, comment)
        changed[name] = {
            "before": before,
            "after": expression_state(entity),
        }

    proof_formulas = {
        "TRX43_EXPR_REAR_WING_DRAG_N": (
            "-EXPVAL(TRX25_EXPR_WING_AERO_FX_N)",
            "Positive drag magnitude for proof plots.",
        ),
        "TRX43_EXPR_REAR_WING_DOWNFORCE_N": (
            "-EXPVAL(TRX25_EXPR_WING_AERO_FZ_N)",
            "Positive downforce magnitude for proof plots.",
        ),
        "TRX43_EXPR_WING_TRACKING_ERROR_DEG": (
            "EXPVAL(TRX27_EXPR_WING_TARGET_AOA_DEG)-"
            "EXPVAL(TRX27_EXPR_WING_ACTUAL_AOA_DEG)",
            "Target minus actual physical incidence, degrees.",
        ),
        "TRX43_EXPR_WING_ACTUAL_RATE_DEG_S": (
            "EXPVAL(TRX25_EXPR_WING_ACTUAL_W_RAD_S)*180/PI",
            "Actual hinge angular rate, deg/s.",
        ),
        "TRX43_EXPR_WING_RANGE_VIOLATION_DEG": (
            "MAX(0,-EXPVAL(TRX25_EXPR_WING_ACTUAL_Q_RAD)*180/PI)+"
            "MAX(0,(EXPVAL(TRX25_EXPR_WING_ACTUAL_Q_RAD)-"
            "1.48352986419518)*180/PI)",
            "Zero inside the intended q=[0,85] deg envelope; positive only on violation.",
        ),
    }
    for name, (text, comment) in proof_formulas.items():
        entity, before = set_or_create_expression(
            model, expressions, name, text, comment
        )
        changed[name] = {
            "before": before,
            "after": expression_state(entity),
        }

    return changed


def force_marker_state(force) -> dict:
    ref = getattr(force, "RefMarker", None)
    return {
        "active": bool(force.Active),
        "base_owner": str(force.BaseMarker.OwnerBody.Name),
        "base_marker": str(force.BaseMarker.Name),
        "action_owner": str(force.ActionMarker.OwnerBody.Name),
        "action_marker": str(force.ActionMarker.Name),
        "reference_owner": (
            str(ref.OwnerBody.Name) if ref is not None else None
        ),
        "reference_marker": str(ref.Name) if ref is not None else None,
    }


def configure_entities(model) -> dict:
    joints = by_name(model.JointCollection)
    forces = by_name(model.ForceCollection)
    expressions = by_name(model.ExpressionCollection)
    bodies = by_name(model.BodyCollection)

    required = (
        WING_JOINT,
        WING_SERVO,
        WING_REGULARIZER,
        WING_AERO,
        UNSAFE_FORCE,
    )
    missing = [
        name
        for name in required
        if name not in joints and name not in forces
    ]
    if missing:
        raise KeyError(f"Missing required entities: {missing}")

    joint = IJointRevolute(joints[WING_JOINT])
    servo = IForceAxialRotational(forces[WING_SERVO])
    regularizer = IForceSpringRotational(forces[WING_REGULARIZER])
    aero = IForceTranslational(forces[WING_AERO])
    unsafe = IForceTranslational(forces[UNSAFE_FORCE])
    chassis = IBody(bodies["CHASSIS"])
    driver_ref = by_name(chassis.MarkerCollection)["driver_ref"]

    before = {
        "joint": {
            "use_motion": bool(joint.UseMotion),
            "use_initial_condition": bool(joint.UseInitialCondition),
            "strict_initial_condition": bool(
                joint.UseStrictlyEnforceInitialConditions
            ),
            "initial_position_rad": float(joint.InitialPosition.Value),
            "initial_velocity_rad_s": float(joint.InitialVelocity.Value),
        },
        "servo_active": bool(servo.Active),
        "regularizer_active": bool(regularizer.Active),
        "aero": force_marker_state(aero),
        "unsafe_force": force_marker_state(unsafe),
    }

    joint.Active = True
    joint.UseMotion = False
    joint.UseInitialCondition = True
    joint.UseStrictlyEnforceInitialConditions = True
    joint.InitialPosition.Value = INITIAL_Q_RAD
    joint.InitialVelocity.Value = 0.0
    joint.Comment = (
        "Stage43 active hinge. Prescribed state-dependent Motion is disabled. "
        "q is torque-servo controlled and target-bounded to 0..85 deg."
    )

    servo.Expression = expressions["TRX25_EXPR_WING_SERVO_TORQUE_N_MM"]
    servo.Active = True
    servo.UseActionOnly = False
    servo.Comment = (
        "Stage43 bounded active-wing torque servo. Aerodynamic vehicle load is "
        "not applied to this light visual wing body."
    )

    regularizer.Stiffness.Coefficient.Value = (
        REGULARIZER_STIFFNESS_N_MM_PER_RAD
    )
    regularizer.Damping.Coefficient.Value = (
        SERVO_VELOCITY_GAIN_N_MM_S_PER_RAD
    )
    regularizer.FreeAngle.Value = INITIAL_Q_RAD
    regularizer.PreTorque.Value = 0.0
    regularizer.Active = True
    regularizer.Comment = (
        "Stage43 rotational stiffness/damping paired with the velocity servo; "
        "suppresses overshoot and supplies actual-rate feedback."
    )

    aero.RefMarker = driver_ref
    aero.ExpressionFX = expressions["TRX25_EXPR_WING_AERO_FX_N"]
    aero.ExpressionFY = expressions["TRX25_EXPR_WING_AERO_FY_N"]
    aero.ExpressionFZ = expressions["TRX25_EXPR_WING_AERO_FZ_N"]
    aero.Active = True
    aero.ForceDisplayUse = True
    try:
        aero.ForceDisplay = ForceDisplay.Action
        aero.ForceDisplayColor = 0x00A5FF
    except Exception:
        pass
    aero.Comment = (
        "Stage43 equivalent aerodynamic resultant at the chassis pressure "
        "centre, resolved in CHASSIS.driver_ref. This preserves the correct "
        "vehicle force/moment without creating an artificial hinge moment."
    )

    unsafe.Active = False
    unsafe.Comment = (
        "Stage43 disabled: user-added force joined the moving wing to a remote "
        "chassis marker and generated a large artificial hinge moment/360deg spin."
    )

    after = {
        "joint": {
            "use_motion": bool(joint.UseMotion),
            "use_initial_condition": bool(joint.UseInitialCondition),
            "strict_initial_condition": bool(
                joint.UseStrictlyEnforceInitialConditions
            ),
            "initial_position_rad": float(joint.InitialPosition.Value),
            "initial_position_deg": math.degrees(
                float(joint.InitialPosition.Value)
            ),
            "initial_velocity_rad_s": float(joint.InitialVelocity.Value),
        },
        "servo_active": bool(servo.Active),
        "servo_torque_expression": str(servo.Expression.Name),
        "regularizer_active": bool(regularizer.Active),
        "regularizer_stiffness_n_mm_per_rad": float(
            regularizer.Stiffness.Coefficient.Value
        ),
        "regularizer_damping_n_mm_s_per_rad": float(
            regularizer.Damping.Coefficient.Value
        ),
        "regularizer_free_angle_deg": math.degrees(
            float(regularizer.FreeAngle.Value)
        ),
        "aero": force_marker_state(aero),
        "aero_expressions": {
            "fx": str(aero.ExpressionFX.Name),
            "fy": str(aero.ExpressionFY.Name),
            "fz": str(aero.ExpressionFZ.Name),
        },
        "unsafe_force": force_marker_state(unsafe),
    }
    return {"before": before, "after": after}


def configure_requests(model) -> dict:
    expressions = by_name(model.ExpressionCollection)
    requests = by_name(model.RequestExpressionCollection)
    states = {}
    for name in WING_REQUESTS:
        if name in requests:
            states[name] = {
                "before": bool(requests[name].Active),
            }
            requests[name].Active = True
            states[name]["after"] = bool(requests[name].Active)
        else:
            states[name] = {"missing": True}

    proof_name = "TRX43_REQ_ACTIVE_WING_PROOF"
    proof_channels = (
        expressions["Ex_mea_chassis_vx"],
        expressions["TRX27_EXPR_WING_TARGET_AOA_DEG"],
        expressions["TRX27_EXPR_WING_ACTUAL_AOA_DEG"],
        expressions["TRX43_EXPR_WING_TRACKING_ERROR_DEG"],
        expressions["TRX43_EXPR_REAR_WING_DRAG_N"],
        expressions["TRX43_EXPR_REAR_WING_DOWNFORCE_N"],
    )
    if proof_name in requests:
        proof = IRequestExpression(requests[proof_name])
    else:
        proof = IRequestExpression(
            model.CreateRequestExpression(proof_name, *proof_channels)
        )
    proof.Active = True
    proof.Comment = (
        "Channels: speed km/h, target AOA deg, actual AOA deg, tracking error "
        "deg, positive drag N, positive downforce N."
    )
    states[proof_name] = {"after": bool(proof.Active)}

    health_name = "TRX43_REQ_ACTIVE_WING_HEALTH"
    health_channels = (
        expressions["TRX25_EXPR_WING_SERVO_TORQUE_N_MM"],
        expressions["TRX43_EXPR_WING_ACTUAL_RATE_DEG_S"],
        expressions["TRX43_EXPR_WING_RANGE_VIOLATION_DEG"],
        expressions["TRX28_EXPR_WING_DYNAMIC_PRESSURE_PA"],
        expressions["TRX25_EXPR_WING_CD"],
        expressions["TRX25_EXPR_WING_CL_DOWN"],
    )
    if health_name in requests:
        health = IRequestExpression(requests[health_name])
    else:
        health = IRequestExpression(
            model.CreateRequestExpression(health_name, *health_channels)
        )
    health.Active = True
    health.Comment = (
        "Channels: servo torque Nmm, actual rate deg/s, range violation deg, "
        "dynamic pressure Pa, CD, CL."
    )
    states[health_name] = {"after": bool(health.Active)}
    return states


def reopen_audit(application) -> dict:
    document = IModelDocument(application.OpenModelDocument(OUTPUT_MODEL))
    try:
        document.Activate()
        model = ISubSystem(document.Model)
        expressions = by_name(model.ExpressionCollection)
        joints = by_name(model.JointCollection)
        forces = by_name(model.ForceCollection)
        requests = by_name(model.RequestExpressionCollection)
        joint = IJointRevolute(joints[WING_JOINT])
        servo = IForceAxialRotational(forces[WING_SERVO])
        regularizer = IForceSpringRotational(forces[WING_REGULARIZER])
        aero = IForceTranslational(forces[WING_AERO])
        unsafe = IForceTranslational(forces[UNSAFE_FORCE])
        audit = {
            "validate": bool(document.Validate()),
            "joint_use_motion": bool(joint.UseMotion),
            "joint_initial_q_deg": math.degrees(
                float(joint.InitialPosition.Value)
            ),
            "servo_active": bool(servo.Active),
            "regularizer_active": bool(regularizer.Active),
            "aero": force_marker_state(aero),
            "unsafe_force_active": bool(unsafe.Active),
            "target_text": str(
                IExpression(
                    expressions["TRX25_EXPR_WING_TARGET_Q_RAD"]
                ).Text
            ),
            "fixed_compat_text": str(
                IExpression(expressions["TRX35_EXPR_FIXED_WING_Q_RAD"]).Text
            ),
            "park_text": str(
                IExpression(
                    expressions["TRX27_EXPR_WING_PARK_FRACTION"]
                ).Text
            ),
            "cd_text": str(
                IExpression(expressions["TRX25_EXPR_WING_CD"]).Text
            ),
            "fx_text": str(
                IExpression(expressions["TRX25_EXPR_WING_AERO_FX_N"]).Text
            ),
            "fz_text": str(
                IExpression(expressions["TRX25_EXPR_WING_AERO_FZ_N"]).Text
            ),
            "request_active": {
                name: bool(requests[name].Active)
                if name in requests
                else None
                for name in (*WING_REQUESTS, "TRX43_REQ_ACTIVE_WING_PROOF",
                             "TRX43_REQ_ACTIVE_WING_HEALTH")
            },
        }
        audit["checks"] = {
            "document_valid": audit["validate"],
            "motion_disabled": not audit["joint_use_motion"],
            "initial_q_is_10deg": math.isclose(
                audit["joint_initial_q_deg"], 10.0, abs_tol=1.0e-8
            ),
            "servo_active": audit["servo_active"],
            "regularizer_active": audit["regularizer_active"],
            "aero_active": audit["aero"]["active"],
            "aero_action_is_chassis": (
                audit["aero"]["action_owner"] == "CHASSIS"
            ),
            "aero_reference_is_driver_frame": (
                audit["aero"]["reference_owner"] == "CHASSIS"
                and audit["aero"]["reference_marker"] == "driver_ref"
            ),
            "unsafe_force_disabled": not audit["unsafe_force_active"],
            "invalid_fixed_vx_removed": "VX(" not in audit["fixed_compat_text"],
            "all_requests_active": all(
                value is True for value in audit["request_active"].values()
            ),
        }
        audit["passed"] = all(audit["checks"].values())
        return audit
    finally:
        application.CloseModelDocument(document)


def main():
    bundle = prepare_output_bundle()
    report = {
        "stage": "stage43_active_wing_stable_aero_repair",
        "input_model": INPUT_MODEL,
        "output_model": OUTPUT_MODEL,
        "bundle": bundle,
        "engineering_parameters": {
            "wing_planform_area_m2": WING_AREA_M2,
            "wing_aspect_ratio": WING_ASPECT_RATIO,
            "air_density_kg_m3": AIR_DENSITY_KG_M3,
            "oswald_efficiency": OSWALD_EFFICIENCY,
            "target_hinge_range_deg": [0.0, 85.0],
            "physical_incidence_range_deg": [5.0, 90.0],
            "servo_torque_limit_n_mm": SERVO_TORQUE_LIMIT_N_MM,
            "servo_velocity_gain_n_mm_s_per_rad": (
                SERVO_VELOCITY_GAIN_N_MM_S_PER_RAD
            ),
        },
    }
    application = None
    document = None
    try:
        bridge = dispatch_recurdyn(
            "2023", autoRunFlag=True, maxDelayTimeSec=240
        )
        application = IApplication(bridge.RecurDynApplication)
        document = IModelDocument(
            application.OpenModelDocument(OUTPUT_MODEL)
        )
        document.Activate()
        try:
            document.DeleteResults()
        except Exception:
            pass
        model = ISubSystem(document.Model)
        report["expressions"] = configure_expressions(model)
        report["entities"] = configure_entities(model)
        report["requests"] = configure_requests(model)
        report["validate_before_save"] = bool(document.Validate())
        if not report["validate_before_save"]:
            raise RuntimeError("RecurDyn document validation failed before save")
        if not document.FileSave(OUTPUT_MODEL, True):
            raise RuntimeError("RecurDyn FileSave returned False")
    except Exception as exc:
        report["fatal"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        raise
    finally:
        if document is not None and application is not None:
            try:
                application.CloseModelDocument(document)
            except Exception:
                pass
        if application is not None and os.path.isfile(OUTPUT_MODEL):
            try:
                report["reopen_audit"] = reopen_audit(application)
            except Exception as exc:
                report["reopen_audit_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )
                report["reopen_audit_traceback"] = traceback.format_exc()
        if os.path.isfile(OUTPUT_MODEL):
            report["output_sha256"] = sha256(OUTPUT_MODEL)
            report["output_bytes"] = os.path.getsize(OUTPUT_MODEL)
        report["passed"] = bool(
            report.get("validate_before_save")
            and report.get("reopen_audit", {}).get("passed")
        )
        os.makedirs(os.path.dirname(REPORT), exist_ok=True)
        with open(REPORT, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(json.dumps({
            "output_model": OUTPUT_MODEL,
            "report": REPORT,
            "passed": report["passed"],
            "reopen_checks": report.get("reopen_audit", {}).get("checks"),
        }, ensure_ascii=False, indent=2))

    if not report["passed"]:
        raise RuntimeError("Stage43 active-wing repair did not pass")


if __name__ == "__main__":
    main()
