"""
Playbook library — REST endpoints for per-user reusable instruction packs.

Provides the /api/playbooks* CRUD, the public-playbook opt-in (enable/disable)
and the zip export/import endpoints, registered on the Flask app as a Blueprint
(same convention as service_alerts). The chat-flow integration — staging a
playbook for a turn, per-turn side-table tracking — stays in app.py; storage
logic lives in PlaybookService.
"""
import io
import posixpath
import zipfile
from datetime import datetime

from flask import Blueprint, Response, current_app, jsonify, request

from src.utils.logging import get_logger
from src.utils.playbook_service import (
    MAX_BODY_CHARS,
    MAX_PLAYBOOKS_PER_OWNER,
    PlaybookConflictError,
    PlaybookNotFoundError,
    PlaybookValidationError,
    parse_playbook_md,
    render_playbook_md,
)

logger = get_logger(__name__)

playbooks_bp = Blueprint('playbooks', __name__)

# ---------------------------------------------------------------------------
# Per-app wiring, injected at registration time. Lives on app.config (read via
# current_app) rather than module globals: a second FlaskAppWrapper in the same
# process must not repoint the first app's already-registered routes, and
# blueprint setup methods cannot be re-run after the first registration anyway
# (flask forbids it).
# ---------------------------------------------------------------------------
_STATE_KEY = "PLAYBOOKS_BLUEPRINT_STATE"


def _state():
    return current_app.config[_STATE_KEY]


def _resolve_owner(request_client_id):
    """callable(request_client_id) -> (owner_id, error_response), per app."""
    return _state()["resolve_owner"](request_client_id)


def _playbook_svc():
    """callable() -> PlaybookService, per app."""
    return _state()["playbook_svc"]()


def _auth_enabled() -> bool:
    """Whether auth is enabled for THIS app (gates owner-identity exposure)."""
    return _state()["auth_enabled"]


@playbooks_bp.before_request
def _check_auth():
    """Apply the same auth gate used by the rest of the app.

    require_auth is a decorator that wraps a view; we use a no-op probe
    function so the auth logic (session check, SSO redirect, 401) fires
    and we can intercept a non-passthrough result. Declared at module level
    (not inside register_playbooks) so registering a second app never calls
    a blueprint setup method after the first registration.
    """
    sentinel = object()
    require_auth = _state()["require_auth"]

    @require_auth
    def _probe():
        return sentinel

    result = _probe()
    if result is not sentinel:
        return result  # redirect / 401 from require_auth


# Upload cap: 100 playbooks * 16KB bodies plus zip overhead fits comfortably.
_MAX_PLAYBOOK_UPLOAD_BYTES = 8 * 1024 * 1024
# Per SKILL.md read cap: body cap plus generous frontmatter headroom.
_MAX_PLAYBOOK_MD_BYTES = MAX_BODY_CHARS * 4 + 8192


@playbooks_bp.route('/api/playbooks', methods=['GET'])
def list_playbooks():
    """List the caller's playbooks plus public-shared ones (no bodies)."""
    try:
        owner_id, _err = _resolve_owner(request.args.get("client_id"))
        if _err:
            return _err
        svc = _playbook_svc()
        enabled_ids = svc.list_enabled_playbook_ids(owner_id)
        items = []
        for s in svc.list_playbooks(owner_id, with_bodies=False):
            item = {
                "id": s.id, "name": s.name, "description": s.description,
                "visibility": s.visibility, "is_mine": s.owner_id == owner_id,
                "is_enabled": (s.owner_id == owner_id) or (s.id in enabled_ids),
            }
            # Owner identity is exposed only when auth verifies identities — in
            # anonymous mode an owner id IS the credential and must not leak.
            if _auth_enabled() and s.owner_id != owner_id:
                item["owner"] = s.owner_id
            items.append(item)
        return jsonify({"playbooks": items}), 200
    except Exception as exc:
        logger.error(f"Error listing playbooks: {exc}")
        return jsonify({"error": "Internal server error"}), 500


@playbooks_bp.route('/api/playbooks/<int:playbook_id>', methods=['GET'])
def get_playbook(playbook_id):
    """Fetch one playbook (including body) by id — own or public-shared."""
    try:
        owner_id, _err = _resolve_owner(request.args.get("client_id"))
        if _err:
            return _err
        svc = _playbook_svc()
        s = svc.get_playbook(owner_id, playbook_id, include_public=True)
        payload = {
            "id": s.id, "name": s.name,
            "description": s.description, "body": s.body,
            "visibility": s.visibility, "is_mine": s.owner_id == owner_id,
        }
        if _auth_enabled() and s.owner_id != owner_id:
            payload["owner"] = s.owner_id
        return jsonify(payload), 200
    except PlaybookNotFoundError:
        return jsonify({"error": f"Playbook {playbook_id} not found"}), 404
    except Exception as exc:
        logger.error(f"Error fetching playbook: {exc}")
        return jsonify({"error": "Internal server error"}), 500


@playbooks_bp.route('/api/playbooks/<int:playbook_id>/enable', methods=['POST'])
def enable_playbook(playbook_id):
    """Add a public playbook to the caller's list (opt-in)."""
    try:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            body = {}  # same guard as delete: client_id may come from args instead
        owner_id, _err = _resolve_owner(
            body.get("client_id") or request.args.get("client_id")
        )
        if _err:
            return _err
        _playbook_svc().enable_playbook(owner_id, playbook_id)
        return jsonify({"ok": True}), 200
    except PlaybookNotFoundError:
        return jsonify({"error": f"Playbook {playbook_id} not found"}), 404
    except PlaybookValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.error(f"Error enabling playbook: {exc}")
        return jsonify({"error": "Internal server error"}), 500


@playbooks_bp.route('/api/playbooks/<int:playbook_id>/disable', methods=['POST'])
def disable_playbook(playbook_id):
    """Remove a public playbook from the caller's list (opt-out)."""
    try:
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            body = {}  # same guard as delete: client_id may come from args instead
        owner_id, _err = _resolve_owner(
            body.get("client_id") or request.args.get("client_id")
        )
        if _err:
            return _err
        _playbook_svc().disable_playbook(owner_id, playbook_id)
        return jsonify({"ok": True}), 200
    except Exception as exc:
        logger.error(f"Error disabling playbook: {exc}")
        return jsonify({"error": "Internal server error"}), 500


@playbooks_bp.route('/api/playbooks', methods=['POST'])
def create_playbook():
    """Create a playbook owned by the caller."""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Request body must be valid JSON"}), 400
        owner_id, _err = _resolve_owner(data.get("client_id"))
        if _err:
            return _err
        svc = _playbook_svc()
        s = svc.create_playbook(
            owner_id,
            data.get("name", ""),
            data.get("description", ""),
            data.get("body", ""),
            data.get("visibility") or "private",
        )
        return jsonify({"success": True, "id": s.id, "name": s.name}), 200
    except PlaybookValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except PlaybookConflictError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        logger.error(f"Error creating playbook: {exc}")
        return jsonify({"error": "Internal server error"}), 500


@playbooks_bp.route('/api/playbooks/<int:playbook_id>', methods=['PUT'])
def update_playbook(playbook_id):
    """Update fields of a playbook the caller owns."""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Request body must be valid JSON"}), 400
        owner_id, _err = _resolve_owner(data.get("client_id"))
        if _err:
            return _err
        svc = _playbook_svc()
        s = svc.update_playbook(
            owner_id, playbook_id,
            name=data.get("name"),
            description=data.get("description"),
            body=data.get("body"),
            visibility=data.get("visibility"),
        )
        return jsonify({"success": True, "id": s.id, "name": s.name}), 200
    except PlaybookNotFoundError:
        return jsonify({"error": f"Playbook {playbook_id} not found"}), 404
    except PlaybookValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    except PlaybookConflictError as exc:
        return jsonify({"error": str(exc)}), 409
    except Exception as exc:
        logger.error(f"Error updating playbook: {exc}")
        return jsonify({"error": "Internal server error"}), 500


@playbooks_bp.route('/api/playbooks/<int:playbook_id>', methods=['DELETE'])
def delete_playbook(playbook_id):
    """Delete a playbook the caller owns."""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            data = {}
        owner_id, _err = _resolve_owner(
            data.get("client_id") or request.args.get("client_id"))
        if _err:
            return _err
        svc = _playbook_svc()
        svc.delete_playbook(owner_id, playbook_id)
        return jsonify({"success": True, "deleted": playbook_id}), 200
    except PlaybookNotFoundError:
        return jsonify({"error": f"Playbook {playbook_id} not found"}), 404
    except Exception as exc:
        logger.error(f"Error deleting playbook: {exc}")
        return jsonify({"error": "Internal server error"}), 500


@playbooks_bp.route('/api/playbooks/export', methods=['GET'])
def export_playbooks():
    """Download the caller's OWN playbooks as a zip of `<name>/SKILL.md` folders.

    The layout is the Agent Skills format that claude.ai also accepts, so exports
    are portable beyond archi. Public playbooks owned by others are excluded — an
    export is a backup of what the caller owns, not a copy of the deployment's
    shared library.
    """
    try:
        owner_id, _err = _resolve_owner(request.args.get("client_id"))
        if _err:
            return _err
        svc = _playbook_svc()
        own = [s for s in svc.list_playbooks(owner_id) if s.owner_id == owner_id]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for s in own:
                zf.writestr(
                    f"{s.name}/SKILL.md",
                    render_playbook_md(s.name, s.description, s.body, s.visibility),
                )
        filename = f"archi-playbooks-{datetime.now().strftime('%Y-%m-%d')}.zip"
        return Response(
            buf.getvalue(),
            mimetype="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        logger.error(f"Error exporting playbooks: {exc}")
        return jsonify({"error": "Internal server error"}), 500


def _parse_playbook_upload(upload):
    """Parse an uploaded playbook file into import items.

    Accepts a .zip of `<name>/SKILL.md` folders (the Agent Skills / claude.ai
    layout) or a single SKILL.md. Returns (items, errors): per-entry parse
    failures land in `errors` without sinking the batch. Returns (None, message)
    when the file as a whole is unusable.
    """
    filename = (upload.filename or "").lower()
    blob = upload.read(_MAX_PLAYBOOK_UPLOAD_BYTES + 1)
    if len(blob) > _MAX_PLAYBOOK_UPLOAD_BYTES:
        return None, f"Upload exceeds {_MAX_PLAYBOOK_UPLOAD_BYTES // (1024 * 1024)} MB"
    if filename.endswith(".zip") or blob[:2] == b"PK":
        try:
            zf = zipfile.ZipFile(io.BytesIO(blob))
        except zipfile.BadZipFile:
            return None, "Not a valid zip file"
        items, errors = [], []
        entries = [
            n for n in zf.namelist()
            if posixpath.basename(n) == "SKILL.md" and not n.startswith("__MACOSX/")
        ]
        if not entries:
            return None, "No SKILL.md found in the zip"
        if len(entries) > MAX_PLAYBOOKS_PER_OWNER:
            # reject up front: a partial scan of an oversized zip would silently
            # ignore the tail while looking complete
            return None, f"Too many playbooks in the zip (max {MAX_PLAYBOOKS_PER_OWNER})"
        for entry in entries:
            folder = posixpath.basename(posixpath.dirname(entry))
            try:
                with zf.open(entry) as fh:
                    raw = fh.read(_MAX_PLAYBOOK_MD_BYTES + 1)
                if len(raw) > _MAX_PLAYBOOK_MD_BYTES:
                    errors.append({"name": folder or None, "error": "SKILL.md is too large"})
                    continue
                items.append(parse_playbook_md(raw.decode("utf-8"), fallback_name=folder))
            except UnicodeDecodeError:
                errors.append({"name": folder or None, "error": "SKILL.md is not UTF-8"})
            except PlaybookValidationError as exc:
                errors.append({"name": folder or None, "error": str(exc)})
        return items, errors
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError:
        return None, "File must be a UTF-8 SKILL.md or a zip of playbook folders"
    stem = posixpath.basename(filename)
    stem = stem[:-3] if stem.endswith(".md") else stem
    fallback = "" if stem in ("playbook", "") else stem
    try:
        return [parse_playbook_md(text, fallback_name=fallback)], []
    except PlaybookValidationError as exc:
        return None, str(exc)


@playbooks_bp.route('/api/playbooks/import', methods=['POST'])
def import_playbooks():
    """Import playbooks into the caller's library.

    Accepts a multipart upload (field `file`: a zip of `<name>/SKILL.md` folders
    or a single SKILL.md — the Agent Skills format) with form fields client_id?
    and on_conflict, or the legacy JSON body {client_id?, on_conflict, playbooks:
    [{name, description, body, visibility?}]}. Existing names are skipped unless
    on_conflict='overwrite', which updates them in place. Per-item validation
    errors are reported without aborting the rest of the batch.

    Imported playbooks are ALWAYS created private: a file's public flag must never
    silently publish content deployment-wide. `public_flags_ignored` in the
    response lets the UI tell the user to share from the editor instead.
    """
    try:
        upload = request.files.get("file")
        if upload is not None:
            client_id = request.form.get("client_id")
            on_conflict = request.form.get("on_conflict", "skip")
            items, parse_errors = _parse_playbook_upload(upload)
            if items is None:
                return jsonify({"error": parse_errors}), 400
        else:
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify(
                    {"error": "Request must be a playbook file upload or valid JSON"}
                ), 400
            client_id = data.get("client_id")
            on_conflict = data.get("on_conflict", "skip")
            items = data.get("playbooks")
            if not isinstance(items, list):
                return jsonify({"error": "JSON must contain a 'playbooks' array"}), 400
            parse_errors = []
        owner_id, _err = _resolve_owner(client_id)
        if _err:
            return _err
        if len(items) > MAX_PLAYBOOKS_PER_OWNER:
            return jsonify(
                {"error": f"Too many playbooks in one import (max {MAX_PLAYBOOKS_PER_OWNER})"}
            ), 400
        if on_conflict not in ("skip", "overwrite"):
            return jsonify({"error": "on_conflict must be 'skip' or 'overwrite'"}), 400
        svc = _playbook_svc()
        imported, overwritten, skipped = [], [], []
        errors = list(parse_errors)
        public_flags_ignored = 0
        for raw in items:
            if not isinstance(raw, dict):
                errors.append({"name": None, "error": "each playbook must be an object"})
                continue
            name = raw.get("name")
            bad = [k for k in ("name", "description", "body", "visibility")
                   if raw.get(k) is not None and not isinstance(raw.get(k), str)]
            if bad:
                # report per item instead of letting a TypeError 500 the whole
                # batch after some items have already been committed
                errors.append({
                    "name": name if isinstance(name, str) else None,
                    "error": f"these fields must be strings: {', '.join(bad)}",
                })
                continue
            wants_public = raw.get("visibility") == "public"
            try:
                svc.create_playbook(
                    owner_id, name or "",
                    raw.get("description") or "", raw.get("body") or "",
                    "private",
                )
                imported.append(name)
                if wants_public:
                    public_flags_ignored += 1
            except PlaybookConflictError:
                if on_conflict == "overwrite":
                    try:
                        existing = svc.get_playbook_by_name(owner_id, name)
                        # visibility deliberately untouched: overwriting content
                        # must not flip an already-shared playbook private (or back).
                        svc.update_playbook(
                            owner_id, existing.id,
                            description=raw.get("description"),
                            body=raw.get("body"),
                        )
                        overwritten.append(name)
                        if wants_public and existing.visibility != "public":
                            public_flags_ignored += 1
                    except (PlaybookValidationError, PlaybookNotFoundError) as exc:
                        errors.append({"name": name, "error": str(exc)})
                else:
                    skipped.append(name)
            except PlaybookValidationError as exc:
                errors.append({"name": name, "error": str(exc)})
        return jsonify({
            "imported": imported, "overwritten": overwritten,
            "skipped": skipped, "errors": errors,
            "public_flags_ignored": public_flags_ignored,
        }), 200
    except Exception as exc:
        logger.error(f"Error importing playbooks: {exc}")
        return jsonify({"error": "Internal server error"}), 500


def register_playbooks(app, *, auth_enabled, require_auth, resolve_owner, playbook_svc):
    """Register the playbooks blueprint with a Flask app.

    Parameters
    ----------
    app : Flask
        The Flask application instance.
    auth_enabled : bool
        Whether authentication is enabled (gates owner-identity exposure).
    require_auth : callable
        The ``require_auth`` decorator from FlaskAppWrapper, applied to all
        blueprint routes via ``before_request``.
    resolve_owner : callable
        ``FlaskAppWrapper._resolve_playbook_owner`` — maps a request client_id
        to the verified owner (or a Flask error response).
    playbook_svc : callable
        ``FlaskAppWrapper._playbook_svc`` — returns a PlaybookService on the
        pooled factory when available.
    """
    app.config[_STATE_KEY] = {
        "auth_enabled": auth_enabled,
        "require_auth": require_auth,
        "resolve_owner": resolve_owner,
        "playbook_svc": playbook_svc,
    }
    app.register_blueprint(playbooks_bp)
    logger.info("Registered playbooks blueprint at /api/playbooks")
