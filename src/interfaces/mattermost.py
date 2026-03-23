import json
import os
import time
from pathlib import Path
from threading import Thread

import requests
from authlib.integrations.flask_client import OAuth
from flask import Flask, request as flask_request, jsonify, redirect, session, url_for

from src.archi.archi import archi
from src.archi.pipelines.agents.agent_spec import AgentSpecError, select_agent_spec
from src.data_manager.data_manager import DataManager
from src.utils.env import read_secret
from src.utils.logging import get_logger
from src.utils.config_access import get_full_config
from src.utils.mattermost_auth import MattermostAuthManager
from src.utils.mattermost_token_service import MattermostTokenService
from src.utils.rbac.jwt_parser import get_user_roles
from src.utils.rbac.mattermost_context import mattermost_user_context
from src.utils.rbac.registry import get_registry
from src.utils.rbac.permission_enum import Permission

logger = get_logger(__name__)

class MattermostAIWrapper:
    def __init__(self):
        # initialize and update vector store
        self.data_manager = DataManager(run_ingestion=False)

        # initialize chain
        config = get_full_config()
        services_cfg = config.get("services", {})
        mm_cfg = services_cfg.get("mattermost", {})
        chat_cfg = services_cfg.get("chat_app", {})
        agent_class = mm_cfg.get("agent_class") or chat_cfg.get("agent_class", "QAPipeline")
        agents_dir = mm_cfg.get("agents_dir") or chat_cfg.get("agents_dir")
        agent_spec = None
        if agents_dir:
            try:
                agent_spec = select_agent_spec(Path(agents_dir))
            except AgentSpecError as exc:
                logger.warning(f"Failed to load agent spec: {exc}")
                agent_spec = None
        prompt_overrides = mm_cfg.get("prompts", {})
        self.archi = archi(
            pipeline=agent_class,
            agent_spec=agent_spec,
            default_provider=mm_cfg.get("default_provider") or chat_cfg.get("default_provider"),
            default_model=mm_cfg.get("default_model") or chat_cfg.get("default_model"),
            prompt_overrides=prompt_overrides,
        )

    def __call__(self, post):

        # form the formatted history using the post
        formatted_history = []

        post_str = post['message']
        formatted_history.append(("User", post_str)) 

        # call chain
        answer = self.archi(history=formatted_history)["answer"]
        logger.debug('ANSWER = %s', answer)

        return answer, post_str

class Mattermost:
    """
    Class to go through unresolved posts in Mattermost and suggest answers.
    Filter feed for new posts and propose answers.
    Also filter for new posts that have been resolved and add to vector store.
    For now, just iterate through all posts and send replies for unresolved.
    """
    def __init__(self):

        logger.info('Mattermost::INIT')

        config = get_full_config()
        self.mattermost_config = config.get("services", {}).get("mattermost", None)

        # Auth setup
        auth_config = (self.mattermost_config or {}).get("auth", {})
        pg_config = {
            "password": read_secret("PG_PASSWORD"),
            **config.get("services", {}).get("postgres", {}),
        }
        self.auth_manager = MattermostAuthManager(auth_config, pg_config=pg_config)
        self.auth_enabled = auth_config.get("enabled", False)

        # mattermost webhook for reading questions/sending responses
        self.mattermost_url = 'https://mattermost.web.cern.ch/'
        self.mattermost_webhook = read_secret("MATTERMOST_WEBHOOK")
        self.mattermost_channel_id_read = read_secret("MATTERMOST_CHANNEL_ID_READ")
        self.mattermost_channel_id_write = read_secret("MATTERMOST_CHANNEL_ID_WRITE")
        self.PAK = read_secret("MATTERMOST_PAK")        
        self.mattermost_headers = {
            'Authorization': f'Bearer {self.PAK}',
            'Content-Type': 'application/json'
        }

        logger.debug('mattermost_webhook = %s', self.mattermost_webhook)
        logger.debug('mattermost_channel_id_read = %s', self.mattermost_channel_id_read)
        logger.debug('mattermost_channel_id_write = %s', self.mattermost_channel_id_write)
        logger.debug('PAK = %s', self.PAK)

        # initialize MattermostAIWrapper
        self.ai_wrapper = MattermostAIWrapper()

        #
        self.min_next_post_file = "/root/data/LPC2025/min_next_post.json"

    def post_response(self, response):

#        TODO: support writing in a dedicated mattermost_channel_id_write
#        url = f"{self.mattermost_url}/api/v4/posts"
#        print('GOING TO WRITE HERE: ',url)

#        payload = {
#            "channel_id": self.mattermost_channel_id_write,
#            "message": response
#        }
        # send response to MM
        #r = requests.post(url, data=json.dumps(payload), headers=self.mattermost_headers)
        r = requests.post(self.mattermost_webhook, data=json.dumps({"text": response,"channel" : "town-square"}), headers=self.mattermost_headers)

        return

    def write_min_next_post(self, answered_key):
        try:
            # create directory if it does not exist
            os.makedirs(os.path.dirname(self.min_next_post_file), exist_ok=True)
            with open(self.min_next_post_file, "w") as f:
                json.dump({"answered_id": answered_key}, f)
            logger.info(f"Updated answered_id {answered_key}")
        except Exception as e:
            logger.debug(f"ERROR - Failed to write answered_key to file: {e}")

    def get_active_posts(self):

        content = f"api/v4/channels/{self.mattermost_channel_id_read}/posts"
        r = requests.get(self.mattermost_url + content, headers=self.mattermost_headers)
        active_posts={}
        for id in r.json()["order"]:
            active_posts[id]=r.json()["posts"][id]["message"]
        return active_posts

    def filter_posts(self, posts, excluded_user_id):
        system_types = {
            "system_join_team",
            "system_join_channel",
            "system_add_to_channel",
            "system_leave_team",
            "system_leave_channel",
            "system_remove_from_channel",
        }

        filtered = []

        for post in posts.values():
            if post.get("user_id") == excluded_user_id:
                continue  # Skip this user

            if post.get("type") in system_types:
                continue  # Skip system messages

            filtered.append(post)

        return filtered

    def get_last_post(self):

        content = f"api/v4/channels/{self.mattermost_channel_id_read}/posts"

        r = requests.get(self.mattermost_url + content, headers=self.mattermost_headers)
        data = r.json()
        posts = data.get('posts', {})
        excluded_archi_id = "ajb6wyizpinqir7m16owntod7o"

        filtered_posts = self.filter_posts(posts, excluded_user_id=excluded_archi_id)
        sorted_posts = sorted(filtered_posts, key=lambda x: x['create_at'], reverse=True)

        if sorted_posts:
            latest = sorted_posts[0]
            logger.info(f"User ID: {latest['user_id']}")
            logger.info(f"Message: {latest['message']}")
        else:
            logger.debug("Mattermost: No messages found.")

        return sorted_posts[0]

    def checkAnswerExist(self, tmpID):

        # Check if file exists
        if not os.path.exists(self.min_next_post_file):
            logger.info("File does not exist, creating new one.")
            data = {"answered_id": []}  # Initialize with empty list
            return False
        else:
            # Load existing data
            with open(self.min_next_post_file, "r") as f:
                data = json.load(f)
                logger.info("Loaded data: %s", data)

        answered_ids = data.get("answered_id", [])

        # Ensure it's a list
        if not isinstance(answered_ids, list):
            answered_ids = [answered_ids]

        # Only append if not already in the list (optional)
        if tmpID in answered_ids:
            logger.info(f"{tmpID} already exists")
            return True
        else:
            answered_ids.append(tmpID)
            data["answered_id"] = answered_ids  # Overwrite with new ID
            with open(self.min_next_post_file, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Added {tmpID} for next iterations")
            return False

    # for now just processes "main" posts, i.e. not replies/follow-ups
    def process_posts(self):

        try:
            # get last post
            topic = self.get_last_post()
        except Exception as e:
            logger.error("ERROR - Failed to parse feed due to the following exception:")
            logger.error(str(e))
            return

        if self.checkAnswerExist(topic['id']):
            # no need to answer someone already answered
            logger.info('no need to answer someone already answered')
        else:
            # Build user context from post's user_id (no username in polling mode)
            user_id = topic.get('user_id', '')
            ctx = self.auth_manager.build_context(user_id=user_id)

            # None means db mode and no stored token — prompt user to login
            if ctx is None:
                login_url = self.auth_manager.login_url(user_id)
                self.post_response(
                    f"Hi! To use this bot, please login first: {login_url}\n"
                    "After logging in, send your message again."
                )
                self.write_min_next_post(topic['id'])
                return

            # Entry-level permission check
            if self.auth_enabled:
                registry = get_registry()
                if not registry.has_permission(ctx.roles, Permission.Mattermost.ACCESS):
                    logger.info(
                        f"Mattermost polling: access denied for user_id={user_id!r} "
                        f"(roles={ctx.roles})"
                    )
                    self.post_response("Sorry, you don't have permission to use this bot. Please contact an administrator.")
                    self.write_min_next_post(topic['id'])
                    return

            # Process post with user context set for tool permission checks
            try:
                with mattermost_user_context(ctx):
                    answer, post_str = self.ai_wrapper(topic)
                print('topic', topic, ' \n ANSWER: ', answer)
                self.post_response(answer)
                self.write_min_next_post(topic['id'])

            except Exception as e:
                logger.error(f"ERROR - Failed to process post {topic['id']} due to the following exception:")
                logger.error(str(e))


class MattermostWebhookServer:
    """
    Event-driven alternative to the polling-based Mattermost class.
    Runs a Flask HTTP server that receives messages via an outgoing webhook
    (Mattermost pushes POSTs here) and replies via an incoming webhook.
    No Personal Access Token required.
    """
    def __init__(self):
        logger.info('MattermostWebhookServer::INIT')

        self.mattermost_webhook = read_secret("MATTERMOST_WEBHOOK")
        self.outgoing_token = read_secret("MATTERMOST_OUTGOING_TOKEN")
        self.mattermost_headers = {'Content-Type': 'application/json'}

        self.ai_wrapper = MattermostAIWrapper()

        config = get_full_config()
        mm_config = config.get("services", {}).get("mattermost", {})
        self.port = int(mm_config.get("port", 5000))

        # Auth setup
        auth_config = mm_config.get("auth", {})
        pg_config = {
            "password": read_secret("PG_PASSWORD"),
            **config.get("services", {}).get("postgres", {}),
        }
        self.auth_manager = MattermostAuthManager(auth_config, pg_config=pg_config)
        self.auth_enabled = auth_config.get("enabled", False)

        import secrets as _secrets
        self.app = Flask(__name__)
        self.app.secret_key = read_secret("FLASK_UPLOADER_APP_SECRET_KEY") or _secrets.token_hex(32)
        self.app.add_url_rule('/webhook', 'webhook', self._handle_webhook, methods=['POST'])

        # SSO OAuth routes for Mattermost user authentication
        sso_cfg = auth_config.get('sso', {})
        self._sso_enabled = bool(read_secret("SSO_CLIENT_ID") and read_secret("SSO_CLIENT_SECRET"))
        if self._sso_enabled:
            self._oauth = OAuth(self.app)
            self._oauth.register(
                name='sso',
                client_id=read_secret("SSO_CLIENT_ID"),
                client_secret=read_secret("SSO_CLIENT_SECRET"),
                server_metadata_url=sso_cfg.get(
                    'server_metadata_url',
                    'https://auth.cern.ch/auth/realms/cern/.well-known/openid-configuration',
                ),
                client_kwargs={'scope': 'openid profile email offline_access'},
            )
            self._token_service = MattermostTokenService(
                pg_config=pg_config,
                token_endpoint=sso_cfg.get('token_endpoint', ''),
                session_lifetime_days=int(auth_config.get('session_lifetime_days', 30)),
                roles_refresh_hours=int(auth_config.get('roles_refresh_hours', 24)),
            )
            self.app.add_url_rule('/mattermost-auth', 'mattermost_auth_login', self._mattermost_auth_login)
            self.app.add_url_rule('/mattermost-auth/callback', 'mattermost_auth_callback', self._mattermost_auth_callback)
            logger.info("MattermostWebhookServer: SSO auth routes registered")

    def _handle_webhook(self):
        # Mattermost outgoing webhooks send either application/x-www-form-urlencoded or application/json
        if flask_request.is_json:
            data = flask_request.get_json(silent=True) or {}
        else:
            data = flask_request.form

        token = data.get('token', '')
        if self.outgoing_token and token != self.outgoing_token:
            logger.warning('MattermostWebhookServer: received request with invalid token')
            return jsonify({}), 403

        text = data.get('text', '').strip()
        if not text:
            return jsonify({}), 200

        # Extract user identity from Mattermost outgoing webhook payload
        user_id = data.get('user_id', '')
        username = data.get('user_name', '')
        channel_id = data.get('channel_id', '')

        logger.info(
            f"MattermostWebhookServer: message from @{username} "
            f"(id={user_id}, channel={channel_id}): {text!r}"
        )

        # Build user context — None means db mode with no stored token
        ctx = self.auth_manager.build_context(user_id=user_id, username=username)
        if ctx is None:
            login_url = self.auth_manager.login_url(user_id, username)
            login_msg = (
                f"Hi @{username}! To use this bot, please login first: {login_url}\n"
                "After logging in, send your message again."
            )
            requests.post(
                self.mattermost_webhook,
                data=json.dumps({"text": login_msg}),
                headers=self.mattermost_headers,
            )
            return jsonify({}), 200

        if self.auth_enabled:
            registry = get_registry()
            if not registry.has_permission(ctx.roles, Permission.Mattermost.ACCESS):
                logger.info(
                    f"MattermostWebhookServer: access denied for @{username} "
                    f"(roles={ctx.roles})"
                )
                deny_msg = "Sorry, you don't have permission to use this bot. Please contact an administrator."
                requests.post(
                    self.mattermost_webhook,
                    data=json.dumps({"text": deny_msg}),
                    headers=self.mattermost_headers,
                )
                return jsonify({}), 200

        try:
            post = {'message': text}
            with mattermost_user_context(ctx):
                answer, _ = self.ai_wrapper(post)
            requests.post(self.mattermost_webhook, data=json.dumps({"text": answer}), headers=self.mattermost_headers)
        except Exception as e:
            logger.error(f"MattermostWebhookServer: failed to process message: {e}")

        return jsonify({}), 200

    def _mattermost_auth_login(self):
        """
        Step 1: user clicks the login link from Mattermost.
        Stashes mm_username in session, then redirects to CERN SSO.
        mm_user_id is passed as OAuth state and round-tripped back by SSO.
        """
        mm_user_id = flask_request.args.get('state', '').strip()
        mm_username = flask_request.args.get('username', '').strip()
        if not mm_user_id:
            return "Missing Mattermost user ID", 400
        session['_mm_pending_username'] = mm_username
        redirect_uri = url_for('mattermost_auth_callback', _external=True)
        return self._oauth.sso.authorize_redirect(redirect_uri, state=mm_user_id)

    def _mattermost_auth_callback(self):
        """
        Step 2: CERN SSO redirects back here after the user authenticates.
        Extracts roles from the JWT and stores them in mattermost_tokens.
        """
        try:
            token = self._oauth.sso.authorize_access_token()
            mm_user_id = flask_request.args.get('state', '').strip()
            mm_username = session.pop('_mm_pending_username', '')

            if not mm_user_id:
                return "Missing Mattermost user ID in callback state", 400

            user_info = token.get('userinfo') or self._oauth.sso.userinfo(token=token)
            user_email = user_info.get('email', user_info.get('preferred_username', ''))
            user_roles = get_user_roles(token, user_email)

            self._token_service.store_token(
                mm_user_id=mm_user_id,
                mm_username=mm_username or user_info.get('preferred_username', ''),
                email=user_email,
                roles=user_roles,
                refresh_token=token.get('refresh_token'),
            )

            logger.info(
                f"Mattermost auth successful: @{mm_username} (id={mm_user_id!r}) "
                f"email={user_email!r} roles={user_roles}"
            )
            return (
                "<html><body style='font-family:sans-serif;padding:2em'>"
                "<h2>Login successful!</h2>"
                f"<p>You are now authenticated as <strong>{user_email}</strong> "
                f"with roles: <strong>{', '.join(user_roles)}</strong>.</p>"
                "<p>You can close this tab and return to Mattermost.</p>"
                "</body></html>"
            )
        except Exception as exc:
            logger.error(f"Mattermost auth callback error: {exc}")
            return (
                "<html><body style='font-family:sans-serif;padding:2em'>"
                "<h2>Authentication failed</h2>"
                f"<p>Error: {exc}</p>"
                "<p>Please try clicking the login link in Mattermost again.</p>"
                "</body></html>"
            ), 500

    def run(self, host='0.0.0.0', port=5000):
        logger.info(f'MattermostWebhookServer: starting on {host}:{port}')
        self.app.run(host=host, port=port)
