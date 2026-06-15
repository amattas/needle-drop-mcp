# Root conftest.
# Sandbox note: tests/test_secrets.py is inaccessible in this dev environment
# (sandbox denies stat on paths matching *secret*). The glob below tells pytest
# to skip it during collection so it never attempts the blocked stat.
collect_ignore_glob = ["tests/*secret*"]
