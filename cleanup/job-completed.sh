#!/bin/bash
# Never turn a successful job into a failure because housekeeping was deferred.
/usr/bin/timeout 55s /usr/bin/python3 /usr/local/libexec/github-runner-cleanup/cleanup.py job-completed || true
