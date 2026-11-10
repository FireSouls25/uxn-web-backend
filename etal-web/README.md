# Web-target support files, vendored from the uxn-dsl checkout
# (`uxn-dsl/web/`: ours, not upstream — the Audio device and the
# window-size controller the etal web target splices into every bundle).
#
# Why a copy lives here: etal resolves them next to its own binary
# (`exe_dir/web`), and release tarballs did not include `web/` until
# the packaging fix (uxn-dsl `.github/workflows/etal-ci.yml` now
# ships `etal + vendor + web`). Until a release with that fix lands
# and `bump-compiler.sh` moves us onto it, the Dockerfile and CI copy
# these files next to the fetched binary so `--target web` works from
# a release install. After that, delete this directory and the COPY
# lines that reference it.
#
# Sync: cp ../../uxn-dsl/web/{audio,view}.js .
