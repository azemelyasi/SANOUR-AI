#!/usr/bin/env bash
# build_wheelhouse.sh - SANOUR AI Step 10.1 wheelhouse build and verification.
# Target: Linux x86_64, CPython 3.13.
# Phase 1 (resolve + download) needs internet. Phases 2-4 (install, tests, packaging) do not.
# Any failure stops the script. No ZIP is written unless every gate passes.
# Usage: bash build_wheelhouse.sh <SANOUR_AI_step10_1_review_fix_2.zip> <stage6.zip>
set -euo pipefail

# ---- Self-test mode: proves ZIP lifecycle behaviour without a target machine.
#   bash build_wheelhouse.sh --selftest <step10_1_zip> <stage6_zip>
# Uses SELFTEST_PY (default python3) only for the packaging stage. It is not a target run.
if [ "${1:-}" = "--selftest" ]; then
  [ "$#" -eq 3 ] || { echo "usage: $0 --selftest <step10_1_zip> <stage6_zip>" >&2; exit 2; }
  SELF="$(readlink -f -- "$0")"
  S1="$(readlink -f -- "$2")"
  S2="$(readlink -f -- "$3")"
  PYT="${SELFTEST_PY:-python3}"
  NAME="SANOUR_AI_step10_1_wheelhouse"
  TD="$(mktemp -d)"
  trap 'rm -rf "$TD"' EXIT

  # A. Early failure: an old final ZIP must be gone, and no ZIP may be left behind.
  mkdir -p "$TD/a" && cd "$TD/a"
  printf 'old final zip' > "$NAME.zip"
  printf 'old temp zip' > ".$NAME.tmp.zip"
  if PY=/nonexistent/python3.13 bash "$SELF" "$S1" "$S2" > a.log 2>&1; then
    echo "FAIL selftest A: early failure did not stop the script" >&2; exit 1
  fi
  [ ! -e "$NAME.zip" ] || { echo "FAIL selftest A: old final ZIP survived an early failure" >&2; exit 1; }
  [ ! -e ".$NAME.tmp.zip" ] || { echo "FAIL selftest A: temp ZIP survived" >&2; exit 1; }
  echo "PASS selftest A: old final ZIP removed before the first phase; early failure left no ZIP"

  # Fake, package-only state. Only the packaging stage runs against it.
  mkdir -p "$TD/b/wheelhouse_build/wheelhouse" "$TD/b/wheelhouse_build/logs"
  W="$TD/b/wheelhouse_build"
  printf 'w' > "$W/wheelhouse/example-1.0-py3-none-any.whl"
  printf 'example==1.0 --hash=sha256:00\n' > "$W/requirements-lock.txt"
  "$PYT" - "$W" <<'PYFAKE'
import json, sys
w = sys.argv[1]
gate = {"status": "OK", "skipped": 0, "failed": 0, "errors": 0, "ran": 3, "discovered": 3,
        "passed": 3, "expected": 3, "exit_code": 0}
json.dump(gate, open(w + "/logs/step10_1.json", "w"))
json.dump(gate, open(w + "/logs/stage6.json", "w"))
json.dump([{"file": "example-1.0-py3-none-any.whl", "status": "MATCH"}],
          open(w + "/logs/pypi_hash_check.json", "w"))
PYFAKE

  # Test copies only. The normal script keeps both switches off and reads no environment switch.
  for pat in '^PACKAGE_ONLY_MODE=0$' '^INJECT_POST_ZIP_FAILURE=0$'; do
    [ "$(grep -c "$pat" "$SELF")" -eq 1 ] || { echo "FAIL selftest: switch line not found exactly once: $pat" >&2; exit 1; }
  done
  sed -e 's/^PACKAGE_ONLY_MODE=0$/PACKAGE_ONLY_MODE=1/' "$SELF" > "$TD/pkg_only_copy.sh"
  sed -e 's/^PACKAGE_ONLY_MODE=0$/PACKAGE_ONLY_MODE=1/' -e 's/^INJECT_POST_ZIP_FAILURE=0$/INJECT_POST_ZIP_FAILURE=1/' "$SELF" > "$TD/pkg_only_fault_copy.sh"

  # B. Post-ZIP failure: the final ZIP must not appear, and the temp ZIP must be removed.
  cd "$TD/b"
  printf 'old final zip' > "$NAME.zip"
  if PY="$PYT" bash "$TD/pkg_only_fault_copy.sh" "$S1" "$S2" > b.log 2>&1; then
    echo "FAIL selftest B: injected post-ZIP failure did not stop the script" >&2; exit 1
  fi
  grep -q "injected post-ZIP failure" b.log || { echo "FAIL selftest B: stopped for another reason" >&2; exit 1; }
  [ ! -e "$NAME.zip" ] || { echo "FAIL selftest B: final ZIP exists after a post-ZIP failure" >&2; exit 1; }
  [ ! -e ".$NAME.tmp.zip" ] || { echo "FAIL selftest B: temp ZIP survived a post-ZIP failure" >&2; exit 1; }
  echo "PASS selftest B: post-ZIP failure left neither final nor temp ZIP"

  # C. Control: the same fake state, no injected failure, must produce a verified final ZIP.
  PY="$PYT" bash "$TD/pkg_only_copy.sh" "$S1" "$S2" > c.log 2>&1
  [ -f "$NAME.zip" ] || { echo "FAIL selftest C: control run produced no final ZIP" >&2; exit 1; }
  [ ! -e ".$NAME.tmp.zip" ] || { echo "FAIL selftest C: temp ZIP left after success" >&2; exit 1; }
  unzip -l "$NAME.zip" | grep -q "SHA256SUMS.txt" || { echo "FAIL selftest C: ZIP lacks SHA256SUMS.txt" >&2; exit 1; }
  echo "PASS selftest C: control run produced a verified final ZIP"
  # D. Real post-ZIP verification failure. The temporary copy tampers with the extracted package
  # after the ZIP is built and extracted, so the checksum check must fail. The normal script is untouched.
  TARGET='unzip -q "$TMP_ZIP" -d "$POST"'
  TAMPER='printf tampered >> "$POST/$PKG_NAME/REPORT.txt"'
  [ "$(grep -c -F -x -- "$TARGET" "$SELF")" -eq 1 ] || { echo "FAIL selftest D: anchor not found exactly once" >&2; exit 1; }
  TARGET="$TARGET" TAMPER="$TAMPER" awk '{ print } $0 == ENVIRON["TARGET"] { print ENVIRON["TAMPER"] }' \
    "$TD/pkg_only_copy.sh" > "$TD/pkg_only_tamper_copy.sh"
  [ "$(grep -c -F -x -- "$TAMPER" "$TD/pkg_only_tamper_copy.sh")" -eq 1 ] || { echo "FAIL selftest D: tamper line not inserted once" >&2; exit 1; }
  mkdir -p "$TD/d" && rm -rf "$TD/d/wheelhouse_build" && cp -r "$TD/b/wheelhouse_build" "$TD/d/"
  cd "$TD/d"
  printf 'old final zip' > "$NAME.zip"
  if PY="$PYT" bash "$TD/pkg_only_tamper_copy.sh" "$S1" "$S2" > d.log 2>&1; then
    echo "FAIL selftest D: tampered post-ZIP package was accepted" >&2; exit 1
  fi
  grep -q "did NOT match" d.log || { echo "FAIL selftest D: failure was not the checksum check" >&2; tail -20 d.log >&2; exit 1; }
  grep -q ": FAILED" "$TD/d/wheelhouse_build/logs/post_zip_check.log" || { echo "FAIL selftest D: post-ZIP checksum log has no FAILED entry" >&2; exit 1; }
  [ ! -e "$NAME.zip" ] || { echo "FAIL selftest D: final ZIP exists after a post-ZIP verification failure" >&2; exit 1; }
  [ ! -e ".$NAME.tmp.zip" ] || { echo "FAIL selftest D: temp ZIP survived a post-ZIP verification failure" >&2; exit 1; }
  echo "PASS selftest D: real post-ZIP checksum failure; script failed, no final ZIP, temp ZIP removed"

  echo "PASS selftest: all lifecycle cases behaved as specified (not a target run)"
  exit 0
fi

# ---- Normal run. Any ZIP from an earlier run is removed before anything else runs.
# Test-only switches. They are off here, and only the self-test's temporary copies turn them on.
PACKAGE_ONLY_MODE=0
INJECT_POST_ZIP_FAILURE=0
PKG_NAME="SANOUR_AI_step10_1_wheelhouse"
ROOT_DIR="$(pwd)"
FINAL_ZIP="$ROOT_DIR/$PKG_NAME.zip"
TMP_ZIP="$ROOT_DIR/.$PKG_NAME.tmp.zip"
rm -f "$FINAL_ZIP" "$TMP_ZIP"
trap 'rc=$?; rm -f "$TMP_ZIP"; exit $rc' EXIT
trap 'exit 130' INT TERM HUP
for leaked in SANOUR_PACKAGE_ONLY SANOUR_TEST_FAIL_AFTER_ZIP; do
  if [ -n "${!leaked:-}" ]; then
    echo "FAIL: $leaked is set in the environment. Test switches are not accepted in a normal run. Unset it." >&2
    exit 1
  fi
done

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <step10_1_zip> <stage6_zip>" >&2
  exit 2
fi
STEP_ZIP="$(readlink -f -- "$1")"
STAGE6_ZIP="$(readlink -f -- "$2")"
[ -f "$STEP_ZIP" ] || { echo "FAIL: input not found: $1" >&2; exit 2; }
[ -f "$STAGE6_ZIP" ] || { echo "FAIL: input not found: $2" >&2; exit 2; }

PY="${PY:-python3.13}"
command -v "$PY" >/dev/null 2>&1 || { echo "FAIL: '$PY' not found" >&2; exit 1; }
if [ "$PACKAGE_ONLY_MODE" = "1" ]; then
  echo "WARNING: package-only test mode. This is not a Linux x86_64 / CPython 3.13 run." >&2
else
  "$PY" -c 'import sys, platform
ok = (sys.version_info[:2] == (3, 13) and sys.implementation.name == "cpython"
      and platform.machine() == "x86_64" and platform.system() == "Linux")
sys.exit(0 if ok else 1)' || { echo "FAIL: need Linux x86_64 CPython 3.13" >&2; exit 1; }
fi

# Refuse to run if a real key is present. The script never reads or uses it.
if [ -n "${OPENAI_API_KEY:-}" ]; then
  echo "FAIL: OPENAI_API_KEY is set in this shell. Unset it and rerun. The script will not continue with it." >&2
  exit 1
fi
# Remove every OpenAI and proxy setting that could affect the run.
for v in OPENAI_API_KEY OPENAI_BASE_URL OPENAI_CUSTOM_HEADERS OPENAI_ORG_ID OPENAI_PROJECT_ID \
         OPENAI_LOG OPENAI_ADMIN_KEY OPENAI_WEBHOOK_SECRET OPENAI_API_TYPE OPENAI_API_VERSION \
         HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy \
         SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE; do
  unset "$v"
done
export PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PIP_CONFIG_FILE=/dev/null

WORK="$ROOT_DIR/wheelhouse_build"
if [ "$PACKAGE_ONLY_MODE" = "1" ]; then
  [ -d "$WORK/wheelhouse" ] || { echo "FAIL: package-only needs an existing $WORK" >&2; exit 1; }
else
  rm -rf "$WORK"
  mkdir -p "$WORK/extract/step10_1" "$WORK/extract/stage6" "$WORK/logs"
fi
mkdir -p "$WORK/logs"
cd "$WORK"

cat > tools.py <<'PYEOF'
#!/usr/bin/env python3
"""Helpers for build_wheelhouse.sh. Standard library only. Every failure exits non-zero."""
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys


def fail(msg):
    print("FAIL: " + msg, file=sys.stderr)
    sys.exit(1)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def norm(name):
    return name.lower().replace("_", "-").replace(".", "-")


def find_root(extract_dir):
    """Find the single project root (pyproject.toml + src/sanour_ai + tests) inside an extracted ZIP."""
    roots = []
    for pyproject in pathlib.Path(extract_dir).rglob("pyproject.toml"):
        d = pyproject.parent
        if (d / "src" / "sanour_ai").is_dir() and (d / "tests").is_dir():
            roots.append(d)
    if len(roots) != 1:
        fail(f"expected exactly one project root under {extract_dir}, found {len(roots)}: {roots}")
    print(roots[0])


def resolved(report_json, out_path):
    """Pinned requirements from pip's resolution report (real metadata, not guessed)."""
    data = json.load(open(report_json, encoding="utf-8"))
    pins = set()
    for item in data["install"]:
        m = item["metadata"]
        if norm(m["name"]) == "sanour-ai":
            continue
        pins.add(f"{norm(m['name'])}=={m['version']}")
    if not pins:
        fail("resolution report lists no dependencies")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(pins)) + "\n")
    print(f"resolved {len(pins)} pinned requirements")


def make_lock(wheel_dir, pins_file, lock_out, sums_out):
    names = sorted(os.listdir(wheel_dir))
    if not names:
        fail("wheelhouse is empty")
    not_wheels = [n for n in names if not n.endswith(".whl")]
    if not_wheels:
        fail(f"non-wheel files present (source packages are not allowed): {not_wheels}")
    groups = {}
    sums = []
    for base in names:
        parts = base[:-4].split("-")
        if len(parts) < 5:
            fail(f"unexpected wheel filename: {base}")
        pin = f"{norm(parts[0])}=={parts[1]}"
        digest = sha256(os.path.join(wheel_dir, base))
        groups.setdefault(pin, []).append(digest)
        sums.append(f"{digest}  wheelhouse/{base}")
    want = {l.strip() for l in open(pins_file, encoding="utf-8") if l.strip()}
    have = set(groups)
    if want - have:
        fail(f"missing wheels for: {sorted(want - have)}")
    if have - want:
        fail(f"wheels not in the resolved set: {sorted(have - want)}")
    with open(lock_out, "w", encoding="utf-8") as f:
        for pin in sorted(groups):
            f.write(pin + " " + " ".join(f"--hash=sha256:{h}" for h in groups[pin]) + "\n")
    with open(sums_out, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(sums)) + "\n")
    print(f"lock: {len(groups)} pins, {len(names)} wheels, all resolved pins present")


def verify_install(venv_dir, lock_file):
    """Installed set must equal the lock (except pip), and imports must come from this venv."""
    py = os.path.join(venv_dir, "bin", "python")
    listed = json.loads(subprocess.run([py, "-m", "pip", "list", "--format=json"],
                                       capture_output=True, text=True, check=True).stdout)
    installed = {(norm(d["name"]), d["version"]) for d in listed if norm(d["name"]) != "pip"}
    locked = {tuple(l.split(" ")[0].split("==")) for l in open(lock_file, encoding="utf-8") if l.strip()}
    if installed != locked:
        fail(f"installed set differs from lock: extra={sorted(installed - locked)} "
             f"missing={sorted(locked - installed)}")
    probe = ("import json,sys,openai,httpx2,pydantic,pydantic_core,jiter,anyio,httpcore2,truststore;"
             "print(json.dumps({'prefix': sys.prefix, 'files': [m.__file__ for m in "
             "[openai,httpx2,pydantic,pydantic_core,jiter,anyio,httpcore2,truststore]]}))")
    info = json.loads(subprocess.run([py, "-c", probe], capture_output=True, text=True,
                                     check=True).stdout)
    prefix = os.path.realpath(venv_dir)
    if os.path.realpath(info["prefix"]) != prefix:
        fail(f"interpreter prefix {info['prefix']} is not the clean venv {prefix}")
    for f in info["files"]:
        if not os.path.realpath(f).startswith(prefix + os.sep):
            fail(f"module loaded from outside the clean venv: {f}")
    print(f"installed set matches lock ({len(installed)} packages); all imports come from the clean venv")


def count_tests(root, python, pythonpath):
    """Count tests by discovery in the ZIP's own tests/ tree (skipped tests are included)."""
    code = ("import unittest,sys;"
            "s=unittest.defaultTestLoader.discover(start_dir='tests', top_level_dir='.');"
            "print(s.countTestCases())")
    env = dict(os.environ, PYTHONPATH=pythonpath, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run([python, "-c", code], cwd=root, capture_output=True, text=True, env=env)
    if proc.returncode != 0 or not proc.stdout.strip().isdigit():
        fail(f"test discovery failed in {root}: {proc.stderr[-400:]}")
    return int(proc.stdout.strip())


def run_tests(root, expected, label, python, logdir, extra_pythonpath=""):  # expected unused
    """Run unittest. Gate: exit 0, OK, Ran == discovered count, zero failures/errors/skips."""
    os.makedirs(logdir, exist_ok=True)
    pythonpath = os.pathsep.join(p for p in ["src", ".", extra_pythonpath] if p)
    env = dict(os.environ, PYTHONPATH=pythonpath, PYTHONDONTWRITEBYTECODE="1")
    discovered = count_tests(root, python, pythonpath)
    if discovered == 0:
        fail(f"{label}: discovery found zero tests")
    if expected < 0:
        expected = discovered  # "auto" is reserved for suites without a fixed baseline
    elif discovered != expected:
        fail(f"{label}: expected {expected} tests, discovered {discovered}")
    proc = subprocess.run([python, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"],
                          cwd=root, capture_output=True, text=True, env=env)
    out = proc.stdout + proc.stderr
    log = os.path.join(logdir, f"{label}.log")
    with open(log, "w", encoding="utf-8") as f:
        f.write(out)
    ran_m = re.findall(r"^Ran (\d+) tests?", out, re.M)
    status_m = re.findall(r"^(OK|FAILED)\b(.*)$", out, re.M)
    ran = int(ran_m[-1]) if ran_m else -1
    status, detail = status_m[-1] if status_m else ("NONE", "")

    def count(key):
        m = re.search(rf"{key}=(\d+)", detail)
        return int(m.group(1)) if m else 0

    failures, errors, skipped = count("failures"), count("errors"), count("skipped")
    per_line = re.findall(r" \.\.\. (ok|FAIL|ERROR|skipped)\b", out)
    per_class = {}
    for cls, result in re.findall(r"\(tests\.[\w.]*?\.(\w+)\.\w+\) \.\.\. (ok|FAIL|ERROR|skipped)\b", out):
        per_class.setdefault(cls, {}).setdefault(result, 0)
        per_class[cls][result] += 1
    summary = {
        "label": label, "exit_code": proc.returncode, "ran": ran, "status": status,
        "discovered": discovered,
        "passed": per_line.count("ok"), "failed": failures + per_line.count("FAIL"),
        "errors": errors + per_line.count("ERROR"), "skipped": skipped, "expected": expected,
        "per_class": per_class, "log": log,
    }
    with open(os.path.join(logdir, f"{label}.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
    print(json.dumps({k: v for k, v in summary.items() if k != "per_class"}, indent=2))
    gate_ok = (proc.returncode == 0 and status == "OK" and ran == discovered and failures == 0
               and errors == 0 and skipped == 0 and summary["passed"] == discovered)
    if not gate_ok:
        fail(f"{label}: gate not met (see {log})")
    print(f"PASS gate: {label}: {summary['passed']} passed, 0 failed, 0 errors, 0 skipped")


def negative_check(log_path, lock_file):
    """The empty-index install must fail because the packages are not available, nothing else."""
    text = open(log_path, encoding="utf-8", errors="replace").read()
    reason = re.search(r"No matching distribution found for|Could not find a version that satisfies the requirement", text)
    if not reason:
        fail(f"negative install failed, but not for a missing-distribution reason; see {log_path}")
    pins = [l.split(" ")[0].split("==")[0] for l in open(lock_file, encoding="utf-8") if l.strip()]
    named = [p for p in pins if re.search(rf"requirement {re.escape(p)}\b|distribution found for {re.escape(p)}\b", text, re.I)]
    if not named:
        fail("negative install failure does not name any locked package")
    print(f"PASS negative control: refused for a missing-distribution reason, naming {named[0]}")


# 3) compare each wheel's SHA256 with the official PyPI record for the same file
def pypi_check(wheel_dir, out_json):
    import urllib.request
    results, failures = [], []
    for base in sorted(os.listdir(wheel_dir)):
        parts = base[:-4].split("-")
        name, ver = norm(parts[0]), parts[1]
        local = sha256(os.path.join(wheel_dir, base))
        url = f"https://pypi.org/pypi/{name}/{ver}/json"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                meta = json.load(r)
        except Exception as exc:  # network or registry error is recorded, and fails the gate
            results.append({"file": base, "status": "UNVERIFIED", "reason": f"lookup failed: {type(exc).__name__}"})
            failures.append(base)
            continue
        match = [u for u in meta.get("urls", []) if u.get("filename") == base]
        if not match:
            results.append({"file": base, "status": "NOT_ON_PYPI", "local_sha256": local})
            failures.append(base)
            continue
        official = match[0]["digests"]["sha256"]
        status = "MATCH" if official == local else "MISMATCH"
        results.append({"file": base, "status": status, "local_sha256": local, "pypi_sha256": official})
        if status != "MATCH":
            failures.append(base)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    for r in results:
        print(f"{r['status']}: {r['file']}")
    if failures:
        fail(f"PyPI hash check did not match for {len(failures)} file(s): {failures}; see {out_json}")
    print(f"PASS PyPI check: all {len(results)} wheels match the official SHA256 for the same file")


REQUIRED_JSON_KEYS = ("discovered", "ran", "passed", "failed", "errors", "skipped", "status",
                      "expected", "exit_code")


def check_json(logs_dir):
    """Independent check: both gate reports carry discovered and agree with what actually ran."""
    for label in ("step10_1", "stage6"):
        path = os.path.join(logs_dir, f"{label}.json")
        if not os.path.isfile(path):
            fail(f"missing report {path}")
        d = json.load(open(path, encoding="utf-8"))
        missing = [k for k in REQUIRED_JSON_KEYS if k not in d]
        if missing:
            fail(f"{label}.json lacks keys: {missing}")
        if d["exit_code"] != 0:
            fail(f"{label}.json: test process exit code is {d['exit_code']}")
        if d["expected"] != d["discovered"]:
            fail(f"{label}.json: expected {d['expected']} != discovered {d['discovered']}")
        if not (d["discovered"] == d["ran"] == d["passed"] and d["discovered"] > 0):
            fail(f"{label}.json: discovered/ran/passed disagree: {d['discovered']}/{d['ran']}/{d['passed']}")
        if d["status"] != "OK" or d["failed"] or d["errors"] or d["skipped"]:
            fail(f"{label}.json is not a clean pass")
        print(f"PASS {label}.json: discovered={d['discovered']} ran={d['ran']} passed={d['passed']} skipped=0")


def pypi_summary(json_path):
    rows = json.load(open(json_path, encoding="utf-8"))
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print(f"{len(rows)} wheels: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


# Exact relative paths that the manifest does not list (it cannot list itself or its own check log).
MANIFEST_EXCLUDED = ("SHA256SUMS.txt", os.path.join("logs", "final_sha256_check.log"))


def check_manifest(pkg_dir, sums_name="SHA256SUMS.txt"):
    """Every file in the package is listed in the manifest, and the manifest lists nothing absent."""
    listed = set()
    for line in open(os.path.join(pkg_dir, sums_name), encoding="utf-8"):
        path = line.split("  ", 1)[1].strip()
        listed.add(path[2:] if path.startswith("./") else path)
    actual = set()
    for root, _, files in os.walk(pkg_dir):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), pkg_dir)
            if rel not in MANIFEST_EXCLUDED:
                actual.add(rel)
    if listed != actual:
        fail(f"manifest mismatch: unlisted={sorted(actual - listed)} absent={sorted(listed - actual)}")
    print(f"PASS manifest: {len(listed)} files listed, none missing or extra")


def selftest_manifest():
    """Build the real package layout in a temp dir, then check pass, extra-file, and missing-file cases."""
    import shutil
    import tempfile

    def build(base):
        os.makedirs(os.path.join(base, "wheelhouse"))
        os.makedirs(os.path.join(base, "logs"))
        for rel, body in [("wheelhouse/example-1.0-py3-none-any.whl", "w"),
                          ("requirements-lock.txt", "l"), ("REPORT.txt", "r"),
                          ("logs/step10_1.json", "{}"), ("logs/stage6.json", "{}")]:
            with open(os.path.join(base, rel), "w", encoding="utf-8") as f:
                f.write(body)
        lines = []
        for root, _, files in os.walk(base):
            for f in files:
                rel = os.path.relpath(os.path.join(root, f), base)
                lines.append(f"{sha256(os.path.join(base, rel))}  ./{rel}")
        with open(os.path.join(base, "SHA256SUMS.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(lines)) + "\n")
        with open(os.path.join(base, "logs", "final_sha256_check.log"), "w", encoding="utf-8") as f:
            f.write("OK\n")

    def expect(ok, base, label):
        try:
            check_manifest(base)
            passed = True
        except SystemExit:
            passed = False
        if passed != ok:
            fail(f"selftest {label}: expected {'pass' if ok else 'fail'}")
        print(f"PASS selftest {label}")

    root = tempfile.mkdtemp(prefix="manifest_selftest_")
    try:
        real = os.path.join(root, "real")
        build(real)
        expect(True, real, "real layout passes")
        with open(os.path.join(real, "wheelhouse", "extra.whl"), "w", encoding="utf-8") as f:
            f.write("x")
        expect(False, real, "extra wheel fails")
        os.remove(os.path.join(real, "wheelhouse", "extra.whl"))
        os.remove(os.path.join(real, "REPORT.txt"))
        expect(False, real, "missing listed file fails")
        build(os.path.join(root, "again"))
        with open(os.path.join(root, "again", "logs", "unexpected.log"), "w", encoding="utf-8") as f:
            f.write("x")
        expect(False, os.path.join(root, "again"), "extra log fails")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print("PASS manifest selftest: all cases behave as specified")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "find-root":
        find_root(*args)
    elif cmd == "resolved":
        resolved(*args)
    elif cmd == "make-lock":
        make_lock(*args)
    elif cmd == "verify-install":
        verify_install(*args)
    elif cmd == "check-json":
        check_json(*args)
    elif cmd == "pypi-summary":
        pypi_summary(*args)
    elif cmd == "selftest-manifest":
        selftest_manifest()
    elif cmd == "check-manifest":
        check_manifest(*args)
    elif cmd == "negative-check":
        negative_check(*args)
    elif cmd == "pypi-check":
        pypi_check(*args)
    elif cmd == "run-tests":
        root, label, python, logdir = args[0], args[2], args[3], args[4]
        expected = int(args[1]) if args[1] != "auto" else -1
        run_tests(root, expected, label, python, logdir, *args[5:6])
    else:
        fail(f"unknown command {cmd}")
PYEOF
if [ "$PACKAGE_ONLY_MODE" != "1" ]; then
unzip -q "$STEP_ZIP" -d extract/step10_1
unzip -q "$STAGE6_ZIP" -d extract/stage6
STEP_ROOT="$(readlink -f -- "$("$PY" tools.py find-root extract/step10_1)")"
STAGE_ROOT="$(readlink -f -- "$("$PY" tools.py find-root extract/stage6)")"
echo "step10_1 project root: $STEP_ROOT"
echo "stage6 project root:   $STAGE_ROOT"

echo "== phase 1: resolve from pyproject.toml and real metadata (needs internet)"
"$PY" -m venv resolve
resolve/bin/python -m pip install -q --upgrade pip
resolve/bin/python -m pip install --dry-run --ignore-installed --no-cache-dir \
  --report resolve.json "${STEP_ROOT}[openai,dev]" > logs/resolve.log 2>&1
"$PY" tools.py resolved resolve.json pins.txt

echo "== phase 1: download binary wheels only (needs internet)"
mkdir -p wheelhouse
"$PY" -m venv dl
dl/bin/python -m pip install -q --upgrade pip
dl/bin/python -m pip download --no-cache-dir --only-binary=:all: --no-deps \
  --python-version 3.13 --implementation cp --abi cp313 \
  --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 --platform linux_x86_64 \
  -d wheelhouse -r pins.txt > logs/download.log 2>&1

echo "== phase 1: lock file and checksums"
"$PY" tools.py make-lock wheelhouse pins.txt requirements-lock.txt wheel_sums.tmp
sha256sum -c wheel_sums.tmp > logs/wheel_sha256_check.log
rm -f wheel_sums.tmp
"$PY" tools.py pypi-check wheelhouse logs/pypi_hash_check.json

echo "== phase 2: clean venv, offline install from wheelhouse only"
CLEAN_PY="$WORK/clean/bin/python"
"$PY" -m venv clean
"$CLEAN_PY" -m pip install --no-index --find-links "$WORK/wheelhouse" --no-cache-dir \
  --no-deps --require-hashes -r requirements-lock.txt > logs/install_clean.log 2>&1
"$CLEAN_PY" -m pip check > logs/pip_check.log 2>&1
"$PY" tools.py verify-install "$WORK/clean" requirements-lock.txt
"$CLEAN_PY" -m pip freeze > logs/installed_freeze.txt

echo "== phase 2: negative control. Install from an empty index must be refused."
mkdir -p empty-index
"$PY" -m venv negative
if "$WORK/negative/bin/python" -m pip install --no-index --find-links "$WORK/empty-index" \
     --no-cache-dir --no-deps --require-hashes -r requirements-lock.txt > logs/negative_install.log 2>&1; then
  echo "FAIL: install succeeded from an empty index; isolation not proven" >&2
  exit 1
fi
"$PY" tools.py negative-check logs/negative_install.log requirements-lock.txt

echo "== phase 3: tests (every gate is mandatory; skipped counts as failure)"
"$PY" tools.py run-tests "$STEP_ROOT" auto step10_1 "$CLEAN_PY" "$WORK/logs"
"$PY" tools.py run-tests "$STAGE_ROOT" 158 stage6 "$CLEAN_PY" "$WORK/logs"
"$CLEAN_PY" -m compileall -q "$STEP_ROOT/src" "$STEP_ROOT/tests" > logs/compileall.log 2>&1
echo "PASS: compileall"
fi

echo "== phase 4: pre-packaging checks"
[ -s requirements-lock.txt ] || { echo "FAIL: lock file empty" >&2; exit 1; }
[ "$(ls wheelhouse | grep -c '\.whl$')" -gt 0 ] || { echo "FAIL: no wheels" >&2; exit 1; }
"$PY" -c "import os,sys; bad=[f for f in os.listdir('wheelhouse') if not f.endswith('.whl')]; sys.exit(1 if bad else 0)" \
  || { echo "FAIL: non-wheel files present in wheelhouse" >&2; exit 1; }
"$PY" tools.py check-json logs
"$PY" tools.py selftest-manifest
PYPI_SUMMARY="$("$PY" tools.py pypi-summary logs/pypi_hash_check.json)"


STAGE="$WORK/stage/$PKG_NAME"
rm -rf "$WORK/stage" && mkdir -p "$STAGE"
cp -r wheelhouse requirements-lock.txt logs "$STAGE/"
cat > "$STAGE/REPORT.txt" <<REPEOF
SANOUR AI Step 10.1 wheelhouse report
generated (UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)
host: $(uname -srm)
interpreter: $("$PY" -c 'import sys,platform;print(sys.version.split()[0], sys.implementation.name, platform.machine(), platform.system())')
step10_1 input: $(basename "$STEP_ZIP") sha256=$(sha256sum "$STEP_ZIP" | cut -d' ' -f1)
stage6 input:   $(basename "$STAGE6_ZIP") sha256=$(sha256sum "$STAGE6_ZIP" | cut -d' ' -f1)
wheels: $(ls wheelhouse | grep -c '\.whl$')
PyPI hash comparison (logs/pypi_hash_check.json): ${PYPI_SUMMARY}
Test gates: logs/step10_1.json and logs/stage6.json (discovered, ran, passed, failed, errors, skipped, exit_code).
Final checksums: SHA256SUMS.txt covers every file except itself and logs/final_sha256_check.log.
Not verified by this script: Windows; Python 3.14; real OpenAI API; real proxy traffic;
OS-level network isolation (the install uses --no-index instead).
REPEOF
cp "$STAGE/REPORT.txt" REPORT.txt

echo "== manifest check before ZIP"
(cd "$STAGE" && find . -type f ! -name SHA256SUMS.txt ! -path './logs/final_sha256_check.log' -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS.txt)
"$PY" tools.py check-manifest "$STAGE"
(cd "$STAGE" && sha256sum -c SHA256SUMS.txt > logs/final_sha256_check.log)
"$PY" tools.py check-manifest "$STAGE"
(cd "$WORK/stage" && zip -qr "$TMP_ZIP" "$PKG_NAME")

echo "== manifest check after extracting the temporary ZIP"
POST="$WORK/post_zip"
rm -rf "$POST" && mkdir -p "$POST"
unzip -q "$TMP_ZIP" -d "$POST"
(cd "$POST/$PKG_NAME" && sha256sum -c SHA256SUMS.txt > "$WORK/logs/post_zip_check.log")
"$PY" tools.py check-manifest "$POST/$PKG_NAME"
cp "$WORK/logs/post_zip_check.log" "$WORK/post_zip_check.log"

if [ "$INJECT_POST_ZIP_FAILURE" = "1" ]; then
  echo "FAIL: injected post-ZIP failure (test hook)" >&2
  exit 1
fi
mv "$TMP_ZIP" "$FINAL_ZIP"
echo "== done"
sha256sum "$ROOT_DIR/$PKG_NAME.zip"
unzip -l "$ROOT_DIR/$PKG_NAME.zip" | tail -1
