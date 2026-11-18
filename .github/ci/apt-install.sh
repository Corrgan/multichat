#!/bin/sh
# Installs Ubuntu packages for the test jobs and keeps the downloaded .deb files in
# $APT_ARCHIVES, which the workflow restores from the Actions cache. The runners' Ubuntu
# mirror sometimes crawls at a few kB/s; a cold download of Qt/mpv/mesa then ran into
# the job timeout (twice in a row on 18.11.2026). apt still verifies every package.
set -eu
archives=${APT_ARCHIVES:?APT_ARCHIVES must point to the cached .deb directory}
# apt (root) sets up partial/ for its download user _apt only if it owns the directory
sudo mkdir -p "$archives"
sudo chown -R root:root "$archives"
apt_opts="-o Acquire::Retries=3 -o Acquire::http::Timeout=30"

for attempt in 1 2 3; do
  # shellcheck disable=SC2086  # apt_opts is a list of options
  if sudo apt-get $apt_opts update &&
     sudo apt-get $apt_opts -o Dir::Cache::Archives="$archives" \
       -o APT::Keep-Downloaded-Packages=true \
       install -y --no-install-recommends "$@"; then
    # apt leaves root-owned files (partial/ is private to _apt); the cache step
    # runs as the runner user and must be able to read them
    sudo chown -R "$(id -u):$(id -g)" "$archives"
    exit 0
  fi
  # While Ubuntu publishes a security update its mirrors briefly list a package
  # the pool no longer has (404) – refresh the index and try again
  echo "::warning::apt failed (attempt $attempt of 3), trying again in 30 s"
  sleep 30
done
exit 1
