# Public audit evidence

HTML snapshots are stored as deterministic gzip files to preserve their exact
retrieved bytes without whitespace normalization. Reported HTML SHA-256 hashes
and filenames in the original evidence manifest refer to the decompressed bytes.
Use Python gzip or another gzip reader to inspect them. These are inert audit
records, not executable application resources.

`portable-tests.txt` records Linux tooling tests only; it is not an iOS build log.
