#!/usr/bin/env bash
# hermes-backup-container.sh - Linux side of the SHARED per-OS backup container
# contract (windows-port-plan.md section 5). The Windows twin is
# scripts/hermes-backup-container.ps1 and implements exactly the same contract.
#
# CONTRACT
# --------
# HermesBackup carries exactly two files at the repo root, written AND read by
# both OSes:
#   hermes-state.zip      members: <os>/hermes-<os>.zip      + MANIFEST.json
#   omniroute-state.zip   members: <os>/omniroute-data.zip   + MANIFEST.json
# <os> is "linux" or "windows". Each node rewrites ONLY its own member and
# copies every other member byte-for-byte, so neither OS can clobber the other.
# A member is always a complete archive of its own kind (`hermes backup -o` for
# the hermes member, a zip of the OmniRoute data dir for the omniroute member);
# the container only groups them, so `hermes import` always receives a valid
# inner zip (hermes_cli/backup.py refuses anything else).
#
# MANIFEST.json (schema 1), per member:
#   {"schema":1,"updated_at":"<stamp>","members":{
#      "linux":  {"os","hostname","member","created_at","zip_size","sha256","hermes_version"},
#      "windows":{"...same keys..."}}}
# Only member bytes are invariant across rewrites; the writer re-serialises
# MANIFEST.json.
#
# COMMANDS
#   check  --container F [--expect-member M ...]   list the container's members
#   get    --container F --member M --out O        extract one member verbatim
#   put    --container F --member M --source S --out O [--hostname H]
#          [--created-at T] [--hermes-version V] [--require M ...]
#          [--allow-legacy-migrate]
#                                                  replace ONE member, keep the
#                                                  others, refresh MANIFEST.json
# EXIT CODES
#   0 ok | 2 usage | 3 absent (file or member) | 4 unreadable archive
#   5 not a container (foreign/legacy layout)  | 6 invariant violated (refuse to
#   publish a container that lost a member or fails --require)
#
# A non-container archive is never silently migrated: put refuses with rc 5
# unless --allow-legacy-migrate is given (then a FRESH container is built; the
# foreign bytes are NOT carried over as members). get refuses with rc 5 and lets
# the caller decide (the Linux workflow imports a pre-M1 standalone
# hermes-state.zip as-is, which is a valid inner archive; the Windows workflow
# treats it as the one-time bootstrap read).

# Deliberately no `set -e`: every failure path here is classified by an exit
# code, and the caller (a GitHub Actions step running under `bash -e`) must never
# turn a "member absent" case into a hard abort.
set +e
set -u

RC_OK=0
RC_USAGE=2
RC_ABSENT=3
RC_UNREADABLE=4
RC_NOT_CONTAINER=5
RC_INVARIANT=6

PROG=${0##*/}

usage() {
  sed -n '/^# COMMANDS/,/^#   publish a container/p' "$0" | sed 's/^# \{0,1\}//'
}

warn() { printf '%s\n' "$*" >&2; }

# valid_member <path-in-container>: exactly "<os>/<name>.zip", no traversal.
valid_member() {
  case "$1" in
    linux/*|windows/*) ;;
    *) return 1 ;;
  esac
  case "$1" in *..*|*//*|*\\*) return 1 ;; esac
  [ "$(printf '%s' "$1" | tr -cd '/' | wc -c)" -eq 1 ] || return 1
  _base=${1#*/}
  case "$_base" in ''|*[!A-Za-z0-9._-]*) return 1 ;; esac
  return 0
}

# container_check <file> - echo the member paths on stdout, return 0/3/4/5.
container_check() {
  _f="$1"
  [ -f "$_f" ] || return "$RC_ABSENT"
  _list=$(unzip -Z1 "$_f" 2>/dev/null) || return "$RC_UNREADABLE"
  [ -n "$_list" ] || return "$RC_NOT_CONTAINER"
  _found=0
  while IFS= read -r _e; do
    [ -n "$_e" ] || continue
    case "$_e" in
      MANIFEST.json|linux/|windows/) continue ;;
      linux/*|windows/*)
        valid_member "$_e" || return "$RC_NOT_CONTAINER"
        _found=1 ;;
      *) return "$RC_NOT_CONTAINER" ;;
    esac
  done <<EOF
$_list
EOF
  [ "$_found" -eq 1 ] || return "$RC_NOT_CONTAINER"
  printf '%s\n' "$_list" | grep -v '/$' | grep -v '^MANIFEST\.json$'
  return "$RC_OK"
}

# manifest_update <file> <member> <sha256> <size>
manifest_update() {
  python3 - "$1" "$2" "$3" "$4" "$HOSTNAME_ARG" "$CREATED_AT" "$HERMES_VERSION" <<'PY'
import json, os, sys

path, member, sha, size, hostname, created_at, hermes_version = sys.argv[1:8]
osname = member.split("/", 1)[0]

data = {}
if os.path.exists(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception:
        data = {}
if not isinstance(data, dict):
    data = {}
members = data.get("members")
if not isinstance(members, dict):
    members = {}
members[osname] = {
    "os": osname,
    "hostname": hostname,
    "member": member,
    "created_at": created_at,
    "zip_size": int(size),
    "sha256": sha,
    "hermes_version": hermes_version,
}
out = {"schema": 1, "updated_at": created_at, "members": members}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(out, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY
}

abs_path() {
  case "$1" in
    /*) printf '%s' "$1" ;;
    *) printf '%s/%s' "$PWD" "$1" ;;
  esac
}

cmd_check() {
  _members=$(container_check "$CONTAINER"); _rc=$?
  [ "$_rc" -eq "$RC_OK" ] || {
    case "$_rc" in
      "$RC_ABSENT") warn "container not present: $CONTAINER" ;;
      "$RC_UNREADABLE") warn "container is not a readable zip: $CONTAINER" ;;
      "$RC_NOT_CONTAINER") warn "not a per-OS container (legacy/foreign layout): $CONTAINER" ;;
    esac
    return "$_rc"
  }
  printf '%s\n' "$_members"
  for _req in $REQUIRE; do
    if ! printf '%s\n' "$_members" | grep -qxF -- "$_req"; then
      warn "required member absent: $_req"
      return "$RC_ABSENT"
    fi
  done
  return "$RC_OK"
}

cmd_get() {
  valid_member "$MEMBER" || { warn "invalid member path: $MEMBER"; return "$RC_USAGE"; }
  _members=$(container_check "$CONTAINER"); _rc=$?
  [ "$_rc" -eq "$RC_OK" ] || return "$_rc"
  printf '%s\n' "$_members" | grep -qxF -- "$MEMBER" || return "$RC_ABSENT"
  _tmp=$(mktemp -d) || return 1
  unzip -oq "$CONTAINER" "$MEMBER" -d "$_tmp" || { rm -rf "$_tmp"; return "$RC_UNREADABLE"; }
  _out=$(abs_path "$OUT")
  mkdir -p "$(dirname "$_out")"
  mv -f "$_tmp/$MEMBER" "$_out" || { rm -rf "$_tmp"; return 1; }
  rm -rf "$_tmp"
  return "$RC_OK"
}

cmd_put() {
  valid_member "$MEMBER" || { warn "invalid member path: $MEMBER"; return "$RC_USAGE"; }
  [ -f "$SOURCE" ] || { warn "snapshot to store not found: $SOURCE"; return "$RC_ABSENT"; }
  _tmp=$(mktemp -d) || return 1
  _old=""
  if [ -e "$CONTAINER" ]; then
    _old=$(container_check "$CONTAINER"); _rc=$?
    if [ "$_rc" -eq "$RC_NOT_CONTAINER" ] && [ "$ALLOW_LEGACY_MIGRATE" -eq 1 ]; then
      warn "warning: $CONTAINER is not a per-OS container - building a fresh one (foreign bytes are dropped, not carried over)"
      _old=""
      _rc=$RC_OK
    fi
    if [ "$_rc" -ne "$RC_OK" ]; then
      rm -rf "$_tmp"
      warn "existing container unusable (rc=$_rc): $CONTAINER - refusing to publish"
      return "$_rc"
    fi
  fi
  _out=$(abs_path "$OUT")
  # Never touch the input container: a failed run must leave the previous file
  # exactly as it was (fail loud, keep old). The caller passes a ".new" path.
  put_fail() {
    rm -rf "$_tmp"
    case "$_out" in
      "$(abs_path "$CONTAINER")") warn "not removing $_out - it is the input container" ;;
      *) rm -f "$_out" ;;
    esac
    return "$1"
  }
  # 1. carry every surviving member over verbatim (bytes, not re-encoded)
  for _m in $_old; do
    [ "$_m" = "$MEMBER" ] && continue
    unzip -oq "$CONTAINER" "$_m" -d "$_tmp" || {
      warn "cannot read existing member $_m - refusing to publish a container without it"
      put_fail "$RC_UNREADABLE"
      return "$RC_UNREADABLE"
    }
  done
  # 1b. the other OS's MANIFEST entry must survive too
  if [ -n "$_old" ]; then
    unzip -oq "$CONTAINER" MANIFEST.json -d "$_tmp" 2>/dev/null || true
  fi
  # 2. replace this OS's member
  mkdir -p "$_tmp/${MEMBER%%/*}"
  cp -f "$SOURCE" "$_tmp/$MEMBER" || { put_fail 1; return 1; }
  _sha=$(sha256sum "$_tmp/$MEMBER" | awk '{print $1}')
  _size=$(wc -c < "$_tmp/$MEMBER" | tr -d ' ')
  manifest_update "$_tmp/MANIFEST.json" "$MEMBER" "$_sha" "$_size" || { put_fail 1; return 1; }
  # 3. rebuild (store-only: members are already deflated)
  _new_members="$MEMBER"
  for _m in $_old; do
    [ "$_m" = "$MEMBER" ] && continue
    _new_members="$_new_members $_m"
  done
  _new_members=$(printf '%s\n' $_new_members | LC_ALL=C sort)
  rm -f "$_out"
  ( cd "$_tmp" && zip -q -X -0 "$_out" MANIFEST.json $_new_members ) || {
    warn "container rebuild failed"
    put_fail 1
    return 1
  }
  # 4. self-check: the rebuilt container must reproduce exactly what we intended
  _rebuilt=$(container_check "$_out"); _rc=$?
  if [ "$_rc" -ne "$RC_OK" ]; then
    warn "rebuilt container is not valid (rc=$_rc)"
    put_fail "$RC_INVARIANT"
    return "$RC_INVARIANT"
  fi
  for _m in $_old; do
    printf '%s\n' "$_rebuilt" | grep -qxF -- "$_m" || {
      warn "invariant: member $_m is missing from the rebuilt container"
      put_fail "$RC_INVARIANT"
      return "$RC_INVARIANT"
    }
  done
  for _req in $REQUIRE; do
    printf '%s\n' "$_rebuilt" | grep -qxF -- "$_req" || {
      warn "invariant: required member $_req is missing from the rebuilt container"
      put_fail "$RC_INVARIANT"
      return "$RC_INVARIANT"
    }
  done
  mkdir -p "$_tmp/verify"
  for _m in $_rebuilt; do
    unzip -oq "$_out" "$_m" -d "$_tmp/verify" || {
      warn "rebuilt container cannot be read back ($_m)"
      put_fail "$RC_UNREADABLE"
      return "$RC_UNREADABLE"
    }
    if ! cmp -s "$_tmp/$_m" "$_tmp/verify/$_m"; then
      warn "invariant: member $_m does not survive the container rebuild byte-for-byte"
      put_fail "$RC_INVARIANT"
      return "$RC_INVARIANT"
    fi
  done
  rm -rf "$_tmp"
  return "$RC_OK"
}

COMMAND=""
CONTAINER=""
MEMBER=""
SOURCE=""
OUT=""
HOSTNAME_ARG=$(hostname -s 2>/dev/null || echo unknown)
CREATED_AT=$(date -u +%Y%m%dT%H%M%SZ)
HERMES_VERSION=""
REQUIRE=""
ALLOW_LEGACY_MIGRATE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --container|--member|--source|--out|--hostname|--created-at|--hermes-version|--require|--expect-member)
      [ $# -ge 2 ] || { warn "$1 needs a value"; usage; exit "$RC_USAGE"; }
      ;;
  esac
  case "$1" in
    --container) CONTAINER=$2; shift 2 ;;
    --member) MEMBER=$2; shift 2 ;;
    --source) SOURCE=$2; shift 2 ;;
    --out) OUT=$2; shift 2 ;;
    --hostname) HOSTNAME_ARG=$2; shift 2 ;;
    --created-at) CREATED_AT=$2; shift 2 ;;
    --hermes-version) HERMES_VERSION=$2; shift 2 ;;
    --require|--expect-member) REQUIRE="$REQUIRE $2"; shift 2 ;;
    --allow-legacy-migrate) ALLOW_LEGACY_MIGRATE=1; shift ;;
    -h|--help) usage; exit "$RC_OK" ;;
    -*) warn "unknown option: $1"; usage; exit "$RC_USAGE" ;;
    *)
      if [ -z "$COMMAND" ]; then COMMAND=$1; else warn "unexpected argument: $1"; usage; exit "$RC_USAGE"; fi
      shift ;;
  esac
done

case "$COMMAND" in
  check)
    [ -n "$CONTAINER" ] || { usage; exit "$RC_USAGE"; }
    cmd_check
    ;;
  get)
    [ -n "$CONTAINER" ] && [ -n "$MEMBER" ] && [ -n "$OUT" ] || { usage; exit "$RC_USAGE"; }
    cmd_get
    ;;
  put)
    [ -n "$CONTAINER" ] && [ -n "$MEMBER" ] && [ -n "$SOURCE" ] && [ -n "$OUT" ] || { usage; exit "$RC_USAGE"; }
    cmd_put
    ;;
  "")
    usage
    exit "$RC_USAGE"
    ;;
  *)
    warn "unknown command: $COMMAND"
    usage
    exit "$RC_USAGE"
    ;;
esac

exit $?
