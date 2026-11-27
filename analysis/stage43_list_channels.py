from __future__ import annotations

import json
import os

from recurdyn import IApplication, IPlotDocument, dispatch_recurdyn


RPLT = os.path.abspath(os.environ["STAGE43_RPLT"])
OUTPUT = os.path.abspath(os.environ["STAGE43_CHANNEL_RESULT"])
KEYWORDS = tuple(
    value.casefold()
    for value in os.environ.get(
        "STAGE43_CHANNEL_KEYWORDS",
        "brake,accx,throttle,speed_error",
    ).split(",")
    if value
)


def main():
    application = IApplication(
        dispatch_recurdyn(
            "2023", autoRunFlag=True, maxDelayTimeSec=240
        ).RecurDynApplication
    )
    plot = IPlotDocument(
        application.NewPlotDocument("TRX43_CHANNEL_INSPECT")
    )
    try:
        index = int(plot.ImportPlotDataFile(RPLT))
        names = list(plot.GetPlottableNameList(index))
        matches = sorted(
            name
            for name in names
            if any(keyword in name.casefold() for keyword in KEYWORDS)
        )
    finally:
        application.ClosePlotDocument(plot)
    result = {
        "rplt": RPLT,
        "keywords": list(KEYWORDS),
        "matches": matches,
    }
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
