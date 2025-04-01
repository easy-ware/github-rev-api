# server.py
import os
import json
import requests
from github import Github, GithubException, UnknownObjectException, GitReleaseAsset, Repository, GitRelease
from dotenv import load_dotenv
from flask import Flask, request, jsonify, g, Response , render_template # Added Response for plain text
from functools import wraps
from typing import Union, Dict, Any, Optional, List

# --- Initialization ---
load_dotenv()
app = Flask(__name__)
# Optional: Configure Flask settings if needed, e.g., app.config['SECRET_KEY'] = '...'

# --- Constants ---
DEFAULT_README_CONTENT = "Automated by gh-manager API"
REQUESTS_TIMEOUT = 25

# --- Custom Exception Class (Unchanged) ---
class ApiException(Exception):
    """Custom exception class for API errors."""
    def __init__(self, message: str, status_code: int = 400, error_code: str = "BAD_REQUEST", details: Any = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.details = details or message

    def to_dict(self):
        return {
            "success": False,
            "error": {
                "code": self.error_code,
                "message": self.message,
                "details": self.details
            }
        }

# --- Authentication & GitHub Instance (Unchanged) ---
def get_github_instance_from_token(token: str) -> Github:
    """Initialize a Github instance, raising ApiException on failure."""
    if not token:
        raise ApiException("Missing GitHub API token.", status_code=401, error_code="AUTH_REQUIRED")

    try:
        gh = Github(token, timeout=30)
        user = gh.get_user()
        _ = user.login # Test authentication
        gh._raw_token = token
        return gh
    except GithubException as e:
        raise ApiException(
            f"GitHub authentication failed: {e.data.get('message', 'Invalid token or connection issue')}",
            status_code=e.status if e.status in [401, 403] else 500,
            error_code="AUTH_FAILED" if e.status in [401, 403] else "GITHUB_API_ERROR",
            details=e.data
        ) from e
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as req_err:
        raise ApiException(
            f"Network error during GitHub authentication: {req_err}",
            status_code=504,
            error_code="NETWORK_ERROR",
            details=str(req_err)
        ) from req_err
    except Exception as e:
        raise ApiException(
            f"Unexpected error during GitHub authentication: {e}",
            status_code=500,
            error_code="INTERNAL_ERROR",
            details=str(e)
        ) from e

def token_required(f):
    """Decorator to check for X-API-Token and initialize Github instance."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = request.headers.get('X-API-Token')
        if not token:
            auth_header = request.headers.get('Authorization')
            if auth_header and auth_header.startswith('Bearer '):
                token = auth_header.split('Bearer ')[1]

        if not token:
             raise ApiException("X-API-Token or Authorization: Bearer <token> header is required.", status_code=401, error_code="AUTH_REQUIRED")

        try:
            g.gh = get_github_instance_from_token(token)
        except ApiException as auth_err:
            return jsonify(auth_err.to_dict()), auth_err.status_code

        return f(*args, **kwargs)
    return decorated_function

# --- Core Logic Functions (Refactored - upload_asset removed, others unchanged) ---

def get_repo_checked(gh: Github, owner: str, repo_name: str) -> Repository:
    """Get a repo object, raising ApiException/GithubException on failure."""
    repo_full_name = f"{owner}/{repo_name}"
    try:
        repo = gh.get_repo(repo_full_name)
        return repo
    except UnknownObjectException:
        raise ApiException(f"Repository '{repo_full_name}' not found or access denied.", status_code=404, error_code="RESOURCE_NOT_FOUND")
    except GithubException as e:
        raise ApiException(
            f"Error accessing repository '{repo_full_name}': {e.data.get('message', 'Unknown error')}",
            status_code=e.status,
            error_code="GITHUB_API_ERROR",
            details=e.data
        ) from e

def create_repository(gh: Github, name: str, private: bool = False, description: str = "", homepage: str = "", readme_content: Optional[str] = None) -> Dict[str, Any]:
    """Create a new repository and optionally add a README.md. Returns repo info dict."""
    user = gh.get_user()
    repo_info = {}
    try:
        repo = user.create_repo(
            name=name, private=private, description=description, homepage=homepage, auto_init=False
        )
        repo_info = {
            "full_name": repo.full_name, "url": repo.html_url, "id": repo.id,
            "private": repo.private, "description": repo.description, "readme_added": None, "readme_error": None
        }

        if readme_content is not None:
            try:
                repo.create_file(
                    path="README.md", message="Initial commit: Add README.md", content=readme_content
                )
                repo_info["readme_added"] = True
            except GithubException as e_file:
                 repo_info["readme_added"] = False
                 repo_info["readme_error"] = f"{e_file.status} - {e_file.data.get('message', e_file)}"
                 app.logger.warning(f"Repo {repo.full_name} created, but README add failed: {repo_info['readme_error']}")

        return repo_info
    except GithubException as e:
        err_msg = f"Failed to create repository '{name}'"
        details = e.data.get('message', 'Unknown GitHub error')
        error_code = "GITHUB_API_ERROR"
        if e.status == 422 and e.data.get('errors'):
            try:
                 details = "; ".join([err.get('message', '') for err in e.data.get('errors', []) if err.get('message')]) or details
            except Exception: pass
            err_msg = f"Failed to create repository '{name}': Validation failed ({details})"
            error_code = "VALIDATION_ERROR"

        raise ApiException(err_msg, status_code=e.status, error_code=error_code, details=e.data) from e

def upload_file(repo: Repository, path: str, content: str, message: str = "Add file") -> Dict[str, Any]:
    """Uploads a file, returning commit info."""
    try:
        file_commit = repo.create_file(path, message, content, branch=repo.default_branch)
        commit_sha = file_commit['commit'].sha
        return {"path": path, "commit_sha": commit_sha, "status": "uploaded"}
    except GithubException as e:
        details = e.data.get('message', 'Unknown GitHub error')
        status_code=e.status
        error_code="GITHUB_API_ERROR"
        if e.status == 422:
             details = f"File '{path}' might already exist, path might be invalid, or content validation failed. Use update operation?"
             error_code="VALIDATION_ERROR"
        raise ApiException(f"Error uploading '{path}': {details}", status_code=status_code, error_code=error_code, details=e.data) from e

def update_file(repo: Repository, path: str, new_content: str, message: str = "Update file") -> Dict[str, Any]:
    """Updates a file, returning commit info."""
    try:
        contents = repo.get_contents(path, ref=repo.default_branch)
        file_commit = repo.update_file(path, message, new_content, contents.sha, branch=repo.default_branch)
        commit_sha = file_commit['commit'].sha
        return {"path": path, "commit_sha": commit_sha, "status": "updated"}
    except UnknownObjectException:
        raise ApiException(f"Cannot update '{path}': File not found.", status_code=404, error_code="RESOURCE_NOT_FOUND")
    except GithubException as e:
        raise ApiException(f"Error updating '{path}': {e.data.get('message', 'Unknown error')}", status_code=e.status, error_code="GITHUB_API_ERROR", details=e.data) from e

def delete_file(repo: Repository, path: str, message: str = "Delete file") -> Dict[str, Any]:
    """Deletes a file, returning commit info."""
    try:
        contents = repo.get_contents(path, ref=repo.default_branch)
        commit_info = repo.delete_file(path, message, contents.sha, branch=repo.default_branch)
        commit_sha = commit_info['commit'].sha
        return {"path": path, "commit_sha": commit_sha, "status": "deleted"}
    except UnknownObjectException:
         raise ApiException(f"Cannot delete '{path}': File not found.", status_code=404, error_code="RESOURCE_NOT_FOUND")
    except GithubException as e:
         raise ApiException(f"Error deleting '{path}': {e.data.get('message', 'Unknown error')}", status_code=e.status, error_code="GITHUB_API_ERROR", details=e.data) from e

def create_release(repo: Repository, tag_name: str, name: str = "", body: str = "", draft: bool = False, prerelease: bool = False) -> GitRelease:
    """Creates a release, returning the GitRelease object."""
    try:
        release = repo.create_git_release(tag_name, name or tag_name, body, draft, prerelease)
        return release
    except GithubException as e:
        details = e.data.get('message', 'Check tag/permissions')
        error_code = "GITHUB_API_ERROR"
        if e.status == 422:
             details = "Validation failed: Tag might already exist, target commit might be invalid, or name/tag format incorrect."
             error_code = "VALIDATION_ERROR"
        raise ApiException(f"Error creating release for tag '{tag_name}': {details}", status_code=e.status, error_code=error_code, details=e.data) from e

def get_release_checked(repo: Repository, release_id: Union[str, int]) -> GitRelease:
    """Gets a specific release by ID or tag name."""
    try:
        release = repo.get_release(release_id)
        return release
    except UnknownObjectException:
        raise ApiException(f"Release '{release_id}' not found.", status_code=404, error_code="RESOURCE_NOT_FOUND")
    except GithubException as e:
        raise ApiException(f"Error accessing release '{release_id}': {e.data.get('message', 'Unknown error')}", status_code=e.status, error_code="GITHUB_API_ERROR", details=e.data) from e

def delete_release(repo: Repository, release_id: Union[str, int]) -> bool:
    """Deletes a specific release."""
    release = get_release_checked(repo, release_id)
    try:
        release.delete_release()
        return True
    except GithubException as e:
        raise ApiException(f"Error deleting release '{release_id}': {e.data.get('message', 'Unknown error')}", status_code=e.status, error_code="GITHUB_API_ERROR", details=e.data) from e

def get_releases(repo: Repository) -> List[GitRelease]:
    """Lists all releases for a repository."""
    try:
        return list(repo.get_releases())
    except GithubException as e:
        raise ApiException(f"Error listing releases for {repo.full_name}: {e.data.get('message', 'Unknown error')}", status_code=e.status, error_code="GITHUB_API_ERROR", details=e.data) from e



def get_assets(repo: Repository, release_id: Union[str, int]) -> List[GitReleaseAsset]:
    """Lists assets for a specific release."""
    release = get_release_checked(repo, release_id)
    try:
        return list(release.get_assets())
    except GithubException as e:
        raise ApiException(f"Error listing assets for release '{release_id}': {e.data.get('message', 'Unknown error')}", status_code=e.status, error_code="GITHUB_API_ERROR", details=e.data) from e

def get_asset_object(repo: Repository, release_id: Union[str, int], asset_name: str) -> GitReleaseAsset:
    """Gets a specific asset object by name from a release."""
    assets = get_assets(repo, release_id)
    for asset in assets:
        if asset.name == asset_name:
            return asset
    raise ApiException(f"Asset '{asset_name}' not found in release '{release_id}'.", status_code=404, error_code="RESOURCE_NOT_FOUND")

def fetch_final_asset_url(gh: Github, repo: Repository, asset: GitReleaseAsset) -> str:
    """Gets the final (potentially redirected) download URL for an asset."""
    initial_url = asset.browser_download_url
    headers = {'User-Agent': 'gh-manager-api-server'}
    token = getattr(gh, '_raw_token', None)
    target_url = initial_url

    if repo.private:
        if not token:
            raise ApiException("Auth token missing for private asset URL fetch.", status_code=500, error_code="INTERNAL_ERROR", details="Server configuration issue or token not passed correctly.")
        target_url = asset.url
        headers['Authorization'] = f'token {token}'
        headers['Accept'] = 'application/octet-stream'
        app.logger.debug(f"Fetching private asset URL: {target_url}")

    try:
        with requests.head(target_url, headers=headers, allow_redirects=True, timeout=REQUESTS_TIMEOUT, stream=True) as response:
            response.raise_for_status()
            final_url = response.url
            if not final_url:
                 raise ApiException("Failed to resolve final asset URL (empty after redirects).", status_code=500, error_code="INTERNAL_ERROR")
            return final_url
    except requests.exceptions.Timeout:
        raise ApiException("Timeout fetching final asset URL.", status_code=504, error_code="NETWORK_ERROR")
    except requests.exceptions.HTTPError as http_err:
        status = http_err.response.status_code
        err_msg = f"HTTP error {status} fetching final asset URL: {http_err}"
        error_code = "NETWORK_ERROR"
        if repo.private and (status in [401, 403]):
            err_msg += f" (Check token/permissions for API URL {asset.url})"
            error_code = "AUTH_FAILED"
        elif status == 404:
            err_msg += f" (Asset not found at URL {target_url})"
            error_code = "RESOURCE_NOT_FOUND"
        raise ApiException(err_msg, status_code=status, error_code=error_code, details=str(http_err)) from http_err
    except requests.exceptions.RequestException as req_err:
        raise ApiException(f"Network error fetching final asset URL: {req_err}", status_code=502, error_code="NETWORK_ERROR", details=str(req_err)) from req_err
    except Exception as e:
        raise ApiException(f"Unexpected error fetching final asset URL: {e}", status_code=500, error_code="INTERNAL_ERROR", details=str(e)) from e


# --- Flask Error Handlers (Unchanged) ---
@app.errorhandler(ApiException)
def handle_api_exception(error: ApiException):
    """Handles custom API errors."""
    app.logger.error(f"API Error {error.error_code} ({error.status_code}): {error.message} - Details: {error.details}")
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response

@app.errorhandler(GithubException)
def handle_github_exception(error: GithubException):
    """Handles errors originating from the PyGithub library."""
    app.logger.error(f"GitHub API Error ({error.status}): {error.data.get('message', 'Unknown')} - Details: {error.data}")
    api_error = ApiException(
        message=f"GitHub API error: {error.data.get('message', 'Unknown error')}",
        status_code=error.status,
        error_code="GITHUB_API_ERROR",
        details=error.data
    )
    response = jsonify(api_error.to_dict())
    response.status_code = api_error.status_code
    return response

@app.errorhandler(404)
def handle_not_found(error):
    """Handles Flask's default 404 for undefined routes."""
    api_error = ApiException(
        message="The requested URL was not found on the server.",
        status_code=404,
        error_code="ROUTE_NOT_FOUND"
    )
    return jsonify(api_error.to_dict()), 404

@app.errorhandler(405)
def handle_method_not_allowed(error):
    """Handles Flask's default 405 for wrong HTTP method."""
    api_error = ApiException(
        message=f"The method {request.method} is not allowed for the requested URL.",
        status_code=405,
        error_code="METHOD_NOT_ALLOWED"
    )
    return jsonify(api_error.to_dict()), 405

@app.errorhandler(Exception)
def handle_generic_exception(error: Exception):
    """Handles unexpected server errors."""
    app.logger.exception(f"Unhandled Exception: {error}")
    api_error = ApiException(
        message="An unexpected internal server error occurred.",
        status_code=500,
        error_code="INTERNAL_ERROR",
        details=str(error)
    )
    response = jsonify(api_error.to_dict())
    response.status_code = 500
    return response


# --- Flask API Endpoints ---

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "success": True,
        "message": "gh-manager API server is running.",
        "documentation_hint": "Send requests to specific endpoints like /repo/create, /repo/{owner}/{repo}/file etc.",
        "authentication": "Requires 'X-API-Token' or 'Authorization: Bearer <token>' header.",
        "upload_script_endpoint": "/get/v1/upload-script"
    })

# --- Repository Endpoints (Unchanged) ---
@app.route('/repo/create', methods=['POST'])
@token_required
def create_repo_endpoint():
    """Creates a new repository."""
    data = request.get_json()
    if not data: raise ApiException("Request body must be JSON.", 400, "INVALID_INPUT")
    name = data.get('name')
    if not name: raise ApiException("Missing required parameter: 'name'.", 400, "MISSING_PARAMETER")

    private = data.get('private', False)
    description = data.get('description', "")
    homepage = data.get('homepage', "")
    add_readme = data.get('add_readme', False)
    readme_content_custom = data.get('readme_content')

    if not isinstance(private, bool): raise ApiException("'private' must be a boolean.", 400, "INVALID_INPUT")
    if not isinstance(add_readme, bool): raise ApiException("'add_readme' must be a boolean.", 400, "INVALID_INPUT")

    final_readme_content = None
    if readme_content_custom is not None:
        if not isinstance(readme_content_custom, str): raise ApiException("'readme_content' must be a string.", 400, "INVALID_INPUT")
        final_readme_content = readme_content_custom
    elif add_readme:
        final_readme_content = DEFAULT_README_CONTENT

    repo_info = create_repository(g.gh, name, private, description, homepage, final_readme_content)
    return jsonify({"success": True, "data": repo_info}), 201

# --- File Endpoints (Unchanged) ---
@app.route('/repo/<owner>/<repo_name>/file', methods=['POST'])
@token_required
def upload_file_endpoint(owner, repo_name):
    """Uploads a new file to a repository."""
    repo = get_repo_checked(g.gh, owner, repo_name)
    data = request.get_json()
    if not data: raise ApiException("Request body must be JSON.", 400, "INVALID_INPUT")

    path = data.get('path')
    content = data.get('content')
    message = data.get('message', "Add file via gh-manager API")

    if not path: raise ApiException("Missing required parameter: 'path'.", 400, "MISSING_PARAMETER")
    if content is None: raise ApiException("Missing required parameter: 'content'.", 400, "MISSING_PARAMETER")
    if not isinstance(path, str): raise ApiException("'path' must be a string.", 400, "INVALID_INPUT")
    if not isinstance(content, str): raise ApiException("'content' must be a string.", 400, "INVALID_INPUT")
    if not isinstance(message, str): raise ApiException("'message' must be a string.", 400, "INVALID_INPUT")

    result = upload_file(repo, path, content, message)
    return jsonify({"success": True, "data": result}), 201

@app.route('/repo/<owner>/<repo_name>/file/<path:file_path>', methods=['PUT'])
@token_required
def update_file_endpoint(owner, repo_name, file_path):
    """Updates an existing file in a repository."""
    repo = get_repo_checked(g.gh, owner, repo_name)
    data = request.get_json()
    if not data: raise ApiException("Request body must be JSON.", 400, "INVALID_INPUT")

    content = data.get('content')
    message = data.get('message', "Update file via gh-manager API")

    if content is None: raise ApiException("Missing required parameter: 'content'.", 400, "MISSING_PARAMETER")
    if not isinstance(content, str): raise ApiException("'content' must be a string.", 400, "INVALID_INPUT")
    if not isinstance(message, str): raise ApiException("'message' must be a string.", 400, "INVALID_INPUT")

    result = update_file(repo, file_path, content, message)
    return jsonify({"success": True, "data": result}), 200

@app.route('/repo/<owner>/<repo_name>/file/<path:file_path>', methods=['DELETE'])
@token_required
def delete_file_endpoint(owner, repo_name, file_path):
    """Deletes a file from a repository."""
    repo = get_repo_checked(g.gh, owner, repo_name)
    data = request.get_json() or {}
    message = data.get('message', "Delete file via gh-manager API")
    if not isinstance(message, str): raise ApiException("'message' must be a string if provided.", 400, "INVALID_INPUT")

    result = delete_file(repo, file_path, message)
    return jsonify({"success": True, "data": result}), 200

# --- Release Endpoints (Unchanged) ---
@app.route('/repo/<owner>/<repo_name>/release', methods=['POST'])
@token_required
def create_release_endpoint(owner, repo_name):
    """Creates a new release."""
    repo = get_repo_checked(g.gh, owner, repo_name)
    data = request.get_json()
    if not data: raise ApiException("Request body must be JSON.", 400, "INVALID_INPUT")

    tag = data.get('tag')
    if not tag: raise ApiException("Missing required parameter: 'tag'.", 400, "MISSING_PARAMETER")
    if not isinstance(tag, str): raise ApiException("'tag' must be a string.", 400, "INVALID_INPUT")

    name = data.get('name', "")
    body = data.get('body', "")
    draft = data.get('draft', False)
    prerelease = data.get('prerelease', False)

    if not isinstance(name, str): raise ApiException("'name' must be a string.", 400, "INVALID_INPUT")
    if not isinstance(body, str): raise ApiException("'body' must be a string.", 400, "INVALID_INPUT")
    if not isinstance(draft, bool): raise ApiException("'draft' must be a boolean.", 400, "INVALID_INPUT")
    if not isinstance(prerelease, bool): raise ApiException("'prerelease' must be a boolean.", 400, "INVALID_INPUT")

    release = create_release(repo, tag, name, body, draft, prerelease)
    release_data = { "id": release.id, "tag_name": release.tag_name, "name": release.name, "url": release.html_url, "draft": release.draft, "prerelease": release.prerelease, "body": release.body }
    return jsonify({"success": True, "data": release_data}), 201

@app.route('/repo/<owner>/<repo_name>/release/<release_id_or_tag>', methods=['DELETE'])
@token_required
def delete_release_endpoint(owner, repo_name, release_id_or_tag):
    """Deletes a specific release by ID or tag name."""
    repo = get_repo_checked(g.gh, owner, repo_name)
    delete_release(repo, release_id_or_tag)
    return jsonify({"success": True, "data": {"status": "deleted", "release_id_or_tag": release_id_or_tag}}), 200

@app.route('/repo/<owner>/<repo_name>/releases', methods=['GET'])
@token_required
def list_releases_endpoint(owner, repo_name):
    """Lists all releases for a repository."""
    repo = get_repo_checked(g.gh, owner, repo_name)
    releases = get_releases(repo)
    releases_data = [{ "id": r.id, "tag_name": r.tag_name, "name": r.name, "url": r.html_url, "draft": r.draft, "prerelease": r.prerelease } for r in releases]
    return jsonify({"success": True, "data": {"releases": releases_data}}), 200

# --- Asset Endpoints ---
@app.route('/repo/<owner>/<repo_name>/release/<release_id_or_tag>/asset', methods=['POST'])
@token_required
def prepare_asset_upload_endpoint(owner, repo_name, release_id_or_tag):
    """
    Validates parameters for asset upload and returns details
    for a separate client-side upload script. Does NOT upload the file.
    """
    repo = get_repo_checked(g.gh, owner, repo_name)
    release = get_release_checked(repo, release_id_or_tag) # Validates release exists

    data = request.get_json()
    if not data: raise ApiException("Request body must be JSON.", 400, "INVALID_INPUT")

    # This is the path on the CLIENT machine where the file exists
    asset_path_client = data.get('asset_path')
    # Optional name override for the asset on GitHub
    asset_name_remote_override = data.get('asset_name')

    if not asset_path_client:
        raise ApiException("Missing required parameter: 'asset_path' (path on client).", 400, "MISSING_PARAMETER")
    if not isinstance(asset_path_client, str):
        raise ApiException("'asset_path' must be a string.", 400, "INVALID_INPUT")
    if asset_name_remote_override and not isinstance(asset_name_remote_override, str):
        raise ApiException("'asset_name' must be a string if provided.", 400, "INVALID_INPUT")

    # Determine the final asset name for GitHub
    asset_name_final = asset_name_remote_override or os.path.basename(asset_path_client)
    if not asset_name_final:
         raise ApiException("Could not determine asset name (provide 'asset_name' or a valid 'asset_path').", 400, "INVALID_INPUT")

    # Pre-check: Does an asset with this name already exist? (Optional but helpful)
    try:
        existing_assets = release.get_assets()
        for asset in existing_assets:
            if asset.name == asset_name_final:
                 raise ApiException(f"Asset '{asset_name_final}' already exists in release '{release.tag_name}'. Delete it first or use a different name.", 409, "RESOURCE_CONFLICT") # 409 Conflict
    except GithubException as e:
        # Don't fail the whole request, just log a warning if we can't check existing
        app.logger.warning(f"Could not check for existing asset '{asset_name_final}' due to GitHub error: {e}")

    # Prepare the details for the client-side upload script
    upload_details = {
        "repo_full_name": repo.full_name,
        "release_id_or_tag": release_id_or_tag, # Use the ID/tag provided by user for consistency
        "asset_path_local": asset_path_client, # Path on the client
        "asset_name_remote": asset_name_final, # Name for GitHub
        "release_upload_url": release.upload_url # Base URL for uploads to this release
    }

    return jsonify({
        "success": True,
        "data": {
            "message": "Asset validated. Use the following details with your upload script (e.g., from /get/v1/upload-script).",
            "upload_details": upload_details
         }
     }), 200 # OK - Ready for client to upload

@app.route('/repo/<owner>/<repo_name>/release/<release_id_or_tag>/assets', methods=['GET'])
@token_required
def list_assets_endpoint(owner, repo_name, release_id_or_tag):
    """Lists assets for a specific release."""
    repo = get_repo_checked(g.gh, owner, repo_name)
    assets = get_assets(repo, release_id_or_tag)
    assets_data = [{ "id": a.id, "name": a.name, "size": a.size, "content_type": a.content_type, "browser_download_url": a.browser_download_url, "api_url": a.url } for a in assets]
    return jsonify({"success": True, "data": {"assets": assets_data}}), 200

@app.route('/repo/<owner>/<repo_name>/release/<release_id_or_tag>/asset/<asset_name>/url', methods=['GET'])
@token_required
def get_asset_url_endpoint(owner, repo_name, release_id_or_tag, asset_name):
    """Gets download URLs (browser and final direct) for a specific asset."""
    repo = get_repo_checked(g.gh, owner, repo_name)
    asset_obj = get_asset_object(repo, release_id_or_tag, asset_name)

    app.logger.info(f"Fetching final URL for asset '{asset_name}' in release '{release_id_or_tag}'...")
    final_url = fetch_final_asset_url(g.gh, repo, asset_obj)

    result_data = {
         "asset": {
             "id": asset_obj.id, "name": asset_obj.name, "size": asset_obj.size,
             "browser_download_url": asset_obj.browser_download_url,
             "api_url": asset_obj.url,
             "final_direct_url": final_url
             }
    }
    return jsonify({"success": True, "data": result_data}), 200


# --- Helper Endpoint for Upload Script ---
UPLOAD_SCRIPT_CONTENT = """
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# upload.py - Client-side helper to upload GitHub release assets.
# Obtain upload details JSON from the gh-manager API:
# POST /repo/{owner}/{repo}/release/{tag_or_id}/asset
# Then run: python upload.py '<json_details>' --token <your_token>
# Or: python upload.py /path/to/details.json --token <your_token>

import os
import sys
import json
import argparse
try:
    from github import Github, GithubException, UnknownObjectException
except ImportError:
    print("Error: PyGithub library not found. Please install it: pip install PyGithub", file=sys.stderr)
    sys.exit(1)
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None # Ignore if python-dotenv is not installed

# --- Authentication ---
def get_github_instance(token: str) -> Github:
    '''Initialize a Github instance, exit on failure.'''
    if not token:
        print("Error: GitHub token is required (--token or GITHUB_TOKEN).", file=sys.stderr)
        sys.exit(1)
    try:
        # Increased timeout for potentially large uploads
        gh = Github(token, timeout=120)
        print("Verifying token...", end='', flush=True)
        user = gh.get_user()
        _ = user.login # Test authentication
        print(f" Authenticated as {user.login}.")
        return gh
    except GithubException as e:
        print(f"\nError authenticating with GitHub: {e.data.get('message', 'Invalid token or connection issue')} (Status: {e.status})", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nError during authentication: {e}", file=sys.stderr)
        sys.exit(1)

# --- Main Upload Logic ---
def perform_upload(details: dict, token: str):
    '''Performs the actual asset upload using provided details and token.'''
    repo_full_name = details.get("repo_full_name")
    release_id_or_tag = details.get("release_id_or_tag")
    asset_path_local = details.get("asset_path_local")
    asset_name_remote = details.get("asset_name_remote")
    # release_upload_url = details.get("release_upload_url") # PyGithub gets this internally

    # Basic validation of details structure
    if not all([repo_full_name, release_id_or_tag, asset_path_local, asset_name_remote]):
        print("Error: Missing required keys in upload details JSON.", file=sys.stderr)
        print("Expected: repo_full_name, release_id_or_tag, asset_path_local, asset_name_remote", file=sys.stderr)
        print(f"Received: {details}", file=sys.stderr)
        sys.exit(1)

    # Validate local file path
    if not os.path.exists(asset_path_local):
        print(f"Error: Local asset file not found: '{asset_path_local}'", file=sys.stderr)
        sys.exit(1)
    if not os.path.isfile(asset_path_local):
         print(f"Error: Local asset path is not a file: '{asset_path_local}'", file=sys.stderr)
         sys.exit(1)

    print(f"Preparing to upload '{os.path.basename(asset_path_local)}'")
    print(f"  Local Path: {asset_path_local}")
    print(f"  Repo: {repo_full_name}")
    print(f"  Release: {release_id_or_tag}")
    print(f"  Asset Name on GitHub: {asset_name_remote}")

    gh = get_github_instance(token)

    # Get Repository object
    try:
        print(f"Accessing repository '{repo_full_name}'...")
        repo = gh.get_repo(repo_full_name)
    except UnknownObjectException:
        print(f"Error: Repository '{repo_full_name}' not found or access denied.", file=sys.stderr)
        sys.exit(1)
    except GithubException as e:
        print(f"Error accessing repository '{repo_full_name}': {e.data.get('message', e)} (Status: {e.status})", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error getting repository: {e}", file=sys.stderr)
        sys.exit(1)

    # Get Release object
    try:
        print(f"Accessing release '{release_id_or_tag}'...")
        # PyGithub's get_release handles both ID and tag name strings
        release = repo.get_release(release_id_or_tag)
        print(f"Found release ID: {release.id}, Tag: {release.tag_name}")
    except UnknownObjectException:
        print(f"Error: Release '{release_id_or_tag}' not found in repo '{repo_full_name}'.", file=sys.stderr)
        sys.exit(1)
    except GithubException as e:
        print(f"Error accessing release '{release_id_or_tag}': {e.data.get('message', e)} (Status: {e.status})", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error getting release: {e}", file=sys.stderr)
        sys.exit(1)

    # Perform the upload
    try:
        print(f"Uploading '{asset_name_remote}'... (this may take a while)")
        # PyGithub handles getting the correct upload URL and headers
        asset = release.upload_asset(
            path=asset_path_local,
            name=asset_name_remote,
            # content_type=..., # Optional: PyGithub usually guesses correctly
            # label=...        # Optional: Label for the asset
        )
        size_mb = asset.size / (1024*1024)
        print(f"\\nSuccess! Uploaded '{asset.name}' ({size_mb:.2f} MB)")
        print(f"  ID: {asset.id}")
        print(f"  State: {asset.state}")
        print(f"  Browser URL: {asset.browser_download_url}")
    except GithubException as e:
        details_msg = e.data.get('message', 'Unknown GitHub error')
        # Provide more specific feedback for common issues
        if e.status == 422:
            if 'errors' in e.data and isinstance(e.data['errors'], list):
                 err_detail = "; ".join([err.get('message', '') for err in e.data['errors'] if err.get('message')])
                 details_msg = f"Validation failed: {err_detail}"
            else:
                details_msg = f"Asset name '{asset_name_remote}' likely already exists or another validation failed."
        elif e.status == 403:
            details_msg = "Permission denied. Check token scopes (repo access)."
        print(f"\\nError uploading asset: {details_msg} (Status: {e.status})", file=sys.stderr)
        if 'errors' in e.data: print(f"Details: {e.data['errors']}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError: # Should be caught earlier, but just in case
        print(f"\\nError: Local file disappeared before upload? Path: {asset_path_local}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\\nAn unexpected error occurred during upload: {e}", file=sys.stderr)
        # Consider printing traceback in debug mode
        # import traceback
        # traceback.print_exc()
        sys.exit(1)

# --- Argument Parsing & Execution ---
if __name__ == "__main__":
    if load_dotenv:
        load_dotenv() # Load .env for GITHUB_TOKEN if available

    parser = argparse.ArgumentParser(
        description="Upload a GitHub release asset using details from gh-manager API.",
        formatter_class=argparse.RawTextHelpFormatter # Preserve newlines in help
        )
    parser.add_argument(
        "upload_details",
        help="Upload details from the API. Can be:\n"
             "  - A JSON string containing the 'upload_details' object.\n"
             "  - The path to a file containing the JSON response (with the 'upload_details' object).\n"
             "Example JSON structure expected inside 'upload_details':\n"
             '{\n'
             '  "repo_full_name": "owner/repo",\n'
             '  "release_id_or_tag": "v1.0",\n'
             '  "asset_path_local": "/path/on/this/machine/file.zip",\n'
             '  "asset_name_remote": "archive-v1.zip",\n'
             '  "release_upload_url": "..." (Optional, not directly used by this script)\n'
             '}\n'
        )
    parser.add_argument(
        "--token",
        help="GitHub Personal Access Token (PAT).\n"
             "Overrides GITHUB_TOKEN environment variable or .env file.\n"
             "If not provided, attempts to read GITHUB_TOKEN or prompts if possible."
        )

    args = parser.parse_args()

    # --- Get Token ---
    token = args.token or os.getenv('GITHUB_TOKEN')
    if not token:
        try:
            # Check if running interactively before prompting
            if sys.stdin.isatty():
                 token = input("Enter GitHub Personal Access Token: ")
            else:
                 print("Warning: No token found via --token, GITHUB_TOKEN, or .env, and not running interactively.", file=sys.stderr)
        except EOFError: # Handle non-interactive pipe
             pass
        except Exception as e: # Catch potential input issues
             print(f"Warning: Could not prompt for token: {e}", file=sys.stderr)

    if not token:
         print("\nError: GitHub token must be provided via --token, GITHUB_TOKEN env var, .env file, or interactive prompt.", file=sys.stderr)
         sys.exit(1)

    # --- Get Upload Details ---
    details_input = args.upload_details
    upload_details_data = None
    try:
        # Try parsing as file path first
        if os.path.isfile(details_input):
            print(f"Reading upload details from file: {details_input}")
            with open(details_input, 'r') as f:
                api_response = json.load(f)
                # Expecting the details within the 'data' -> 'upload_details' structure from API
                if isinstance(api_response, dict) and 'data' in api_response and 'upload_details' in api_response['data']:
                    upload_details_data = api_response['data']['upload_details']
                else:
                     # Maybe the file contains *only* the upload_details object?
                     upload_details_data = api_response

        # If not a file, try parsing as direct JSON string
        else:
            print("Parsing upload details from argument string...")
            raw_details = json.loads(details_input)
            # Allow passing the nested structure or just the inner object directly
            if isinstance(raw_details, dict) and 'upload_details' in raw_details:
                upload_details_data = raw_details['upload_details']
            else:
                 upload_details_data = raw_details # Assume it's the inner object


        # Final check if we got the required dictionary
        if not isinstance(upload_details_data, dict):
             raise ValueError("Parsed data is not a JSON object.")


    except json.JSONDecodeError:
        print(f"Error: Invalid JSON provided in 'upload_details': Not valid JSON.", file=sys.stderr)
        print(f"Input was: {details_input[:100]}{'...' if len(details_input)>100 else ''}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
         print(f"Error: JSON file not found: {details_input}", file=sys.stderr)
         sys.exit(1)
    except ValueError as ve:
         print(f"Error: Invalid JSON structure in 'upload_details': {ve}", file=sys.stderr)
         sys.exit(1)
    except Exception as e:
         print(f"Error processing upload details input: {e}", file=sys.stderr)
         sys.exit(1)

    # --- Perform Upload ---
    perform_upload(upload_details_data, token)

    print("\nUpload script finished.")
"""

@app.route('/get/v1/upload-script', methods=['GET'])
def get_upload_script():
    """Returns the source code for the client-side upload.py script."""
    # Return as plain text python file
    return Response(UPLOAD_SCRIPT_CONTENT, mimetype='text/plain; charset=utf-8', headers={
        "Content-Disposition": "attachment; filename=upload.py" # Suggest filename
    })


@app.route("/api/v1/docs")
def docs_v1():
    return render_template("docs.html")   

# --- Main Execution (Unchanged) ---
if __name__ == '__main__':
    port = 80
    host = "0.0.0.0"
    app.logger.info(f"Starting server on {host}:{port} )")
    app.run(host=host, port=port)