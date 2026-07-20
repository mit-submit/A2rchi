"""Attachment REST routes (spec §10). Attachments are PRIVATE to a
conversation — the opposite of the admin /api/upload/* knowledge-base paths.

Dependencies are injected by register_attachments (no module globals), so the
blueprint is unit-testable with a bare Flask app. Ownership: client_id comes
from the request, user_id from the session; every operation verifies the
conversation belongs to the caller (404 on failure — not 403 — to avoid
conversation-id enumeration).
"""
from __future__ import annotations

import uuid

from flask import Blueprint, jsonify, request, session

from src.utils.attachment_extraction import AttachmentRejected, extract_attachment
from src.utils.logging import get_logger

logger = get_logger(__name__)


def _session_user_id():
    return session.get("user", {}).get("id") or None


def register_attachments(app, *, service, perm_decorator, create_conversation_fn,
                         attachments_config):
    bp = Blueprint("attachments", __name__)
    cfg = attachments_config or {}
    max_per_conversation = int(cfg.get("max_per_conversation", 20))

    def create_attachment_view():
        upload = request.files.get("file")
        client_id = request.form.get("client_id")
        if upload is None or not upload.filename:
            return jsonify({"error": "No file provided."}), 400
        if not client_id:
            return jsonify({"error": "client_id is required."}), 400
        client_id = client_id.replace("\x00", "")
        user_id = _session_user_id()

        raw_conversation_id = request.form.get("conversation_id")
        if raw_conversation_id:
            try:
                conversation_id = int(raw_conversation_id)
            except ValueError:
                return jsonify({"error": "Conversation not found."}), 404
            if not service.verify_conversation_access(conversation_id, client_id, user_id):
                return jsonify({"error": "Conversation not found."}), 404
            if service.count_for_conversation(conversation_id) >= max_per_conversation:
                return (
                    jsonify({"error": f"This conversation already has "
                                      f"{max_per_conversation} attachments — remove one first."}),
                    409,
                )
        else:
            # Spec D12: attaching on a brand-new chat creates the conversation
            # once the file is accepted below — creating it here would leave
            # a phantom empty conversation behind for a rejected file.
            conversation_id = None

        # Bounded read: never buffer more than the per-file cap (+1 byte to
        # detect overflow) so an oversized post can't slurp the whole request
        # into RAM before the size check. extract_attachment re-checks the
        # length authoritatively below.
        max_file_mb = cfg.get("max_file_mb", 30)
        max_bytes = int(max_file_mb) * 1024 * 1024
        data = upload.read(max_bytes + 1)
        if len(data) > max_bytes:
            return (
                jsonify({"error": f'"{upload.filename}" is larger than the {max_file_mb} MB limit.'}),
                413,
            )
        try:
            result = extract_attachment(upload.filename, data, cfg)
        except AttachmentRejected as exc:
            return jsonify({"error": exc.message}), exc.http_status

        # Per-user storage quota (sum of size_bytes across all conversations).
        # Checked before creating a conversation so an over-quota attach on a
        # brand-new chat leaves no empty conversation behind.
        owner = user_id or client_id
        max_total_mb = int(cfg.get("max_total_mb_per_user", 512))
        if max_total_mb > 0:
            quota_bytes = max_total_mb * 1024 * 1024
            if service.bytes_for_owner(owner) + len(data) > quota_bytes:
                return (
                    jsonify({"error": f"You've reached your {max_total_mb} MB attachment "
                                      f"storage limit — remove some attachments first."}),
                    413,
                )

        if conversation_id is None:
            # The count-cap check above doesn't apply here: a just-created
            # conversation has zero attachments by construction.
            conversation_id = create_conversation_fn("New conversation", client_id, user_id)

        meta = dict(result.meta)
        meta["warnings"] = result.warnings
        created = service.create_attachment(
            conversation_id=conversation_id,
            owner=owner,
            filename=upload.filename,
            kind=result.kind,
            size_bytes=len(data),
            extension=meta.get("extension", ""),
            original_bytes=data,
            extracted_text=result.text,
            extraction_meta=meta,
        )
        return (
            jsonify({
                "attachment_id": created["attachment_id"],
                "created_at": created.get("created_at"),
                "conversation_id": conversation_id,
                "filename": upload.filename,
                "kind": result.kind,
                "size_bytes": len(data),
                "warnings": result.warnings,
                "meta": meta,
            }),
            201,
        )

    def list_attachments_view(conversation_id: int):
        client_id = request.args.get("client_id")
        if not client_id:
            return jsonify({"error": "client_id is required."}), 400
        client_id = client_id.replace("\x00", "")
        if not service.verify_conversation_access(conversation_id, client_id, _session_user_id()):
            return jsonify({"error": "Conversation not found."}), 404
        return jsonify({"attachments": service.list_for_conversation(conversation_id)}), 200

    def delete_attachment_view(attachment_id: str):
        client_id = request.args.get("client_id") or (request.get_json(silent=True) or {}).get("client_id")
        if not client_id:
            return jsonify({"error": "client_id is required."}), 400
        client_id = client_id.replace("\x00", "")
        try:
            uuid.UUID(attachment_id)
        except ValueError:
            return jsonify({"error": "Attachment not found."}), 404
        if not service.delete_attachment(attachment_id, client_id, _session_user_id()):
            return jsonify({"error": "Attachment not found."}), 404
        return "", 204

    bp.add_url_rule(
        "/api/chat/attachments", "create_attachment",
        perm_decorator(create_attachment_view), methods=["POST"],
    )
    bp.add_url_rule(
        "/api/chat/conversations/<int:conversation_id>/attachments", "list_attachments",
        perm_decorator(list_attachments_view), methods=["GET"],
    )
    bp.add_url_rule(
        "/api/chat/attachments/<attachment_id>", "delete_attachment",
        perm_decorator(delete_attachment_view), methods=["DELETE"],
    )
    app.register_blueprint(bp)
