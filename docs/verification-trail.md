# Verification trail

Every claim the site or README states as fact, with its evidence. Method: SIFT. For data claims, the primary
evidence is the archived government file (SHA-256 in `site/public/data/v1/sources.csv`), read at the named cell.
Values marked † are also hand-pinned in `pipeline/tests/pins.toml`, where a test re-reads the raw cell without the
parser on every CI run.

Assessed by: USA Immigration Ledger maintainers, 2026-10-18. Result scale: verified · supported with limits · unresolved.

## Data claims

| # | Claim | Evidence (file · cell) | Result |
|---|---|---|---|
| 1 | 12,807,907 USCIS cases pending at end of FY2026Q3† | quarterly_all_forms_fy2026_q3_v1.xlsx · FY26Q3_All_Forms!G6 | verified |
| 2 | Completions 3,306,130 in FY2024Q2 and 1,583,671 in FY2026Q3 (−52%) | quarterly_all_forms_fy2024_q2.xlsx and fy2026_q3_v1.xlsx · TOTAL row, column F | verified |
| 3 | Completions peaked at 3,306,130 in FY2024Q2 | `total` series, completed, FY2021Q1–FY2026Q3 (USCIS prints completions only from FY2021) | verified |
| 4 | Pending at end of FY2026Q3 is the most in any report we hold | `total` pending, every quarter FY2013Q1–FY2026Q3 | supported with limits: the total line's scope changed over time (FY2021Q4 footnote 21) |
| 5 | N-400 completions 260,023 (FY2025Q3) → 67,565 (FY2026Q3), −74%; median 5.5 → 9.5 months | quarterly_all_forms_fy2025_q3.xlsx and fy2026_q3_v1.xlsx · N-400 row | verified |
| 6 | I-589 receipts 153,786 (FY2025Q2) → 20,159 (FY2026Q3), −87%; 1,392,782 pending | fy2025_q2.xlsx, fy2026_q3_v1.xlsx · I-589 row | verified. Affirmative filings only (USCIS footnote). |
| 7 | I-130 other relatives: median 43.3 months, 1,617,823 pending, FY2026Q3 | fy2026_q3_v1.xlsx · FY26Q3_All_Forms row 10 | verified |
| 8 | FY2024Q3 file repeats FY2024Q2 median processing times for dozens of forms† | e.g. I-129F 8.5479358 at FY24Q2_All_Forms!H8 and FY24Q3_All_Forms!H8 | verified as a fact about the files. Cause unresolved (question 2 to USCIS). |
| 9 | Two I-130 visa-queue files have stale titles | fy2024_q2.xlsx says "As of December 2023"; fy2026_q2_v1.xlsx says "As of March 31, 2025". Their values differ from the files truly dated those quarters. | verified as a fact about the files; correct dates unresolved (question 3) |
| 10 | FY2024 reports print fractional counts, e.g. 2,475,577.752151262 approvals† | quarterly_all_forms_fy2024_q1.xlsx · FY24Q1AllForms!D6 | verified; method unresolved (question 4) |
| 11 | State Dept files list some Egypt/Jordan immediate-relative classes on two rows, and both rows count toward GRAND TOTAL† | e.g. Nov 2023 file Sheet1!C382 (62) and C383 (1); the grand total 45,437 equals the sum of all rows | verified as a fact about the files; reason unresolved (question 5) |
| 13 | Total approvals 1,287,186 in FY2026Q2, the fewest since FY2014Q2 (1,239,622) | fy2026_q2_v1.xlsx TOTAL row; allformtypes_performancedata_fy2014_qtr2.csv | supported with limits: the total line's scope changed over the years (see 4) |
| 14 | N-400 approvals 54,974 in FY2026Q3, the lowest of any quarter since FY2013Q1; previous low 78,008 in FY2020Q3† | fy2026_q3_v1.xlsx · FY26Q3_All_Forms!D56 (row checked by hand: "N-400 / Application for Naturalization", not the military row); Quarterly_All_Forms_FY2020Q4.pdf · page 1!L369 | verified |
| 15 | I-589 approvals: 66 in FY2026Q2, the lowest of any quarter since FY2013Q1† | fy2026_q2_v1.xlsx · FY26Q2_All_Forms!D33 (label printed "I-58912") | verified |
| 16 | USCIS offices were closed to the public for COVID-19 during FY2020Q3 | USCIS public announcements, March 18 to June 4, 2020 | verified from public record, not from our data |
| 17 | PDF-only reports (USCIS FY2019Q3–FY2021Q3; State March 2017–September 2021) were read correctly | Automated tests: every State month's grand total equals its row sum (55 PDFs); USCIS PDF values agree with neighbouring reports 80–98% of the time (spreadsheet pairs: 64–96%) with no wild ratios | supported with limits: an individual PDF cell could still be misread; each cites page, column and position |
| 12 | USCIS's table key defines "-" as "Represents zero or rounds to 0.0" | quarterly_all_forms_fy2023_q4.csv, Table Key block | verified for FY2023Q3 onward; earlier files print no key |

## Not claimed

The site does not say *why* completions fell. The reports carry no explanation, and we haven't obtained one.
Policy changes in 2025–2026 are a plausible line of inquiry, but they are not evidence, so the site makes no
causal claim.

## Right of response

Status: **not yet sent.** The five questions on the methodology page must go to the USCIS Office of Public Affairs
and the State Department press office before the story is promoted. Log each one below with date and channel,
allow at least 48 hours, and record any reply verbatim.

| Question | Sent | Channel | Reply |
|---|---|---|---|
| 1–4 (USCIS) | | | |
| 5 (State Dept) | | | |

## What would change these results

- A republished file for any quarter: the build records it in `revisions.csv`, and the affected claims update automatically.
- An agency explanation for items 8–11: add it to the methodology page and to this trail.
