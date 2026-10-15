# Reference evaluation data is provisioned separately

Binary feature windows and per-record CSVs are not part of the application source
distribution. Supply authorized evaluation data separately when running the
reference validation scripts. The runnable software E2E validator uses synthetic
input and a separately supplied real model checkpoint; it does not need this
reference dataset.

The 2026-10-15 PR removes previously tracked data files from the proposed tree.
Local copies are preserved. Existing published Git history has not been rewritten
or purged; this is not a claim that old public commits no longer contain data.
