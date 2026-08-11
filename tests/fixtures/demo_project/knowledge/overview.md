# demo_project

Six-step single-cell differential expression run, used as the worked example for
`labdocs render` and as the golden fixture for its tests.

Public FASTQ listed in `settings/samples.tsv` is fetched and checksummed, counted
into a raw matrix, filtered on MAD-based QC thresholds, size-factor normalised,
tested against the contrast in `settings/design.tsv`, and rendered as a volcano.

Steps chain through `outputs/<order>_<name>/`; only `deliverables/` leaves the
project. Nothing here is executed by the test suite — the scripts exist so a
human reading the generated report sees real code behind every row.
