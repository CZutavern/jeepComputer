#!/bin/bash
# All offline test suites (no hardware needed).  tests/run_all.sh
cd "$(dirname "$0")/.." || exit 1
status=0
for suite in tests.test_weather tests.test_units tests.test_engine tests.test_vehicle_inputs tests.test_fuel_cal tests.test_ups_acc tests.test_rpm_gauge; do
    echo "=== $suite"
    QT_QPA_PLATFORM=offscreen .venv/bin/python -m "$suite" | tail -1 || status=1
    [ "${PIPESTATUS[0]}" -eq 0 ] || status=1
done
exit $status
