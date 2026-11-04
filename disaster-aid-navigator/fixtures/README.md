# Test fixtures

`declarations.json` holds **synthetic** records in the exact field format of
OpenFEMA's Disaster Declarations Summaries v2. Counties and FIPS codes are real;
the disasters are made up. Disaster numbers 9901-9910 do not exist.

Each record exercises one edge case: open vs closed deadlines, a missing
filing date, statewide declarations, a city and county with the same name,
duplicate rows for one disaster, and records outside the 2-year lookback.
