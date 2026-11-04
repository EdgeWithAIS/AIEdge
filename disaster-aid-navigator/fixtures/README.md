# Test fixtures

`declarations.json` holds **synthetic** records in the exact field format of
OpenFEMA's Disaster Declarations Summaries v2. Counties and FIPS codes are real;
the disasters are made up. Disaster numbers 9901-9910 do not exist.

Each record exercises one edge case: open vs closed deadlines, a missing
filing date, statewide declarations, a city and county with the same name,
duplicate rows for one disaster, and records outside the 2-year lookback.

`zcta_county_sample.txt` holds **real** rows from the U.S. Census Bureau's 2020
ZIP Code Tabulation Area to county relationship file
(`tab20_zcta520_county20_natl.txt`), for the few ZIP codes the tests use.
ZIP 63026 and 63105 each cross a county line.
