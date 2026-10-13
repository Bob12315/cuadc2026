# SITL tests

Tests that require a live simulator belong in this directory. The automated
suite currently exercises simulated command and mission behavior without
starting ArduPilot; the low-speed live acceptance entry point is
`scripts/validate_p0_sitl.py`.

The live script forces the SITL source and creates SEND authorization itself;
running it issues arm/takeoff/motion/land commands to the configured SITL
endpoint. It is not a dry run and has no `--help` or confirmation flag. Verify
that this endpoint belongs to the isolated simulator before using it. Results
are written to `runtime/sitl/p0/acceptance.json`; ordinary pytest collection
does not invoke this script.
