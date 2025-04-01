

# gh-manager API Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A Flask-based REST API server that simplifies programmatic interaction with GitHub repositories for common tasks like repository creation, file management, and release/asset handling. It acts as a wrapper around the [PyGithub](https://pygithub.readthedocs.io/en/latest/) library, providing a stable interface authenticated via GitHub Personal Access Tokens (PATs).

## Overview

This server exposes specific GitHub operations through clear HTTP endpoints. Instead of directly using the `git` command line or complex GitHub SDKs for *every* interaction, `gh-manager` allows you to perform targeted actions via simple API calls, making it ideal for automation scripts, CI/CD pipelines, and backend integrations.

Authentication is handled securely by passing a GitHub PAT in the request headers (`X-API-Token` or `Authorization: Bearer`). Responses are structured JSON objects indicating success or failure with detailed error messages.

A key feature is the two-step workflow for uploading release assets, which avoids the need for the API server itself to have access to local files, enhancing security and flexibility.

## Key Features

*   **Repository Management:** Create new repositories (public or private) with optional README initialization.
*   **File Management:** Upload, update, and delete files within a repository's default branch.
*   **Release Management:** Create release metadata (linked to existing tags), list releases, and delete release metadata.
*   **Release Asset Management:**
    *   Prepare asset uploads (validates parameters, checks conflicts).
    *   List assets for a specific release.
    *   Get asset details, including browser and direct download URLs.
*   **Two-Step Asset Upload:** Securely upload release assets using a dedicated client-side helper script (`upload.py`) provided by the API.
*   **Authentication:** Securely authenticate using GitHub Personal Access Tokens (PATs) via request headers.
*   **Structured Responses:** Clear JSON responses for both success (`{"success": true, "data": {...}}`) and errors (`{"success": false, "error": {...}}`).
*   **Helper Script:** Provides the `upload.py` script via a dedicated endpoint for the asset upload workflow.
*   **Docker Support:** Includes `Dockerfile` and `docker-compose.yml` for easy containerized deployment.

## Use Cases

This API server is particularly useful in scenarios like:

*   **CI/CD Pipelines:**
    *   Automating the creation of a GitHub Release after a successful build.
    *   Uploading build artifacts (binaries, packages) to the corresponding GitHub Release.
    *   Creating temporary repositories for integration tests.
    *   Updating configuration files in a repository based on build parameters.
*   **Automation Scripts:**
    *   Scripts that need to manage files or releases without the overhead of cloning/pulling/pushing via Git.
    *   Tools for onboarding new projects by creating standard repository structures and files.
*   **Backend Services:**
    *   Microservices that need to interact with GitHub for specific tasks without embedding complex Git logic.
    *   Internal developer platforms that provide simplified GitHub operations.
*   **Build Systems:**
    *   Integrating release asset uploads directly into the build process.
*   **Simplified GitHub Interactions:** When you only need a subset of GitHub's functionality and prefer a simple REST interface over the full Git workflow or a complex SDK.

## Prerequisites

*   **Python:** Version 3.12 or newer.
*   **pip:** Python package installer (usually included with Python).
*   **Git:** Required on the machine where you create tags *before* creating releases via the API (the API itself doesn't run `git` commands).
*   **Docker & Docker Compose:** (Optional) Required if running via Docker.
*   **GitHub Personal Access Token (PAT):**
    *   Generate one from [GitHub Developer Settings > Personal access tokens](https://github.com/settings/tokens).
    *   **Required Scopes:** `repo` (Full control of repositories) is essential for almost all operations.
    *   *(Optional Scope):* `delete_repo` if you plan to extend the API to delete repositories (not currently implemented).
    *   **Security:** Treat your PAT like a password! Store it securely (e.g., environment variables, secrets management) and do **not** commit it to your code.

## Getting Started

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/easy-ware/github-rev-api
    cd github-rev-api
    ```

2.  **Choose a Running Method:**

    *   **Method 1: Direct Python Execution**
        *   **(Recommended)** Create and activate a virtual environment:
            ```bash
            python3 -m venv venv
            source venv/bin/activate # On Windows use `venv\Scripts\activate`
            ```
        *   Install dependencies:
            ```bash
            pip install -r requirements/req.txt
            ```
        *   **(Optional)** Configure Environment Variables for `FLASK_DEBUG`:
            Create a `.env` file in the project root or set the environment variable directly:
            ```dotenv
            # .env file example
            FLASK_DEBUG=true # Set to false for production
            ```
            *   `FLASK_DEBUG`: `true` for development (auto-reload, debug info), `false` for production. **Never use `true` in production!**
            *   *Note:* `HOST` and `PORT` are currently set directly in `server.py`'s `if __name__ == '__main__':` block to `0.0.0.0` and `80` respectively when run with `python server.py`. The `.env` file or environment variables will *not* override these specific values when using `python server.py`. However, they *can* be overridden using Flask CLI arguments (see below) or Docker configurations.
        *   Run the server (using the correct filename `server.py`):
            ```bash
            # Runs on 0.0.0.0:80 by default as set in the script
            python server.py
            ```
            *   To run on a different host/port using Flask CLI (requires `FLASK_APP` env var):
                ```bash
                export FLASK_APP=server.py
                flask run --host=127.0.0.1 --port=5000
                ```

    *   **Method 2: Docker Compose**
        *   Ensure Docker and Docker Compose are installed.
        *   **(Optional but Recommended)** Create a `.env` file in the project root to configure the container:
            ```dotenv
            # .env file for Docker
            FLASK_DEBUG=false # Recommended for containerized runs unless debugging
            # HOST=0.0.0.0    # Set inside Dockerfile/docker-compose usually
            # PORT=80       # Internal container port set in Dockerfile/docker-compose
            ```
            *Note:* The `HOST` and `PORT` inside the container are typically managed by the `Dockerfile` (e.g., `EXPOSE 80`) and the `docker-compose.yml` (e.g., `ports: "8080:80"` mapping). The `.env` file primarily controls `FLASK_DEBUG` for the container.
        *   Build and run the services:
            ```bash
            docker compose up
            ```
        *   To run in detached mode (in the background):
            ```bash
            docker compose up -d
            ```
        *   To view logs:
            ```bash
            docker compose logs -f
            ```
        *   To stop the services:
            ```bash
            docker compose down
            ```

## API Usage

The server provides a RESTful API. Interact with it using standard HTTP requests.

*   **Base URL:**
    *   When running `python server.py`: `http://<your-ip-address>:80` (since it defaults to `0.0.0.0:80`). Access might be `http://localhost:80` or `http://127.0.0.1:80` from the same machine.
    *   When running with `docker compose up`: Check the `ports` mapping in `docker-compose.yml`. If it's `"8080:80"`, the base URL will be `http://localhost:8080`.
*   **Authentication:** Required for most endpoints. Provide your GitHub PAT in **every request** using one of these headers:
    *   `X-API-Token: YOUR_GITHUB_PAT`
    *   `Authorization: Bearer YOUR_GITHUB_PAT`
*   **Content Type:** Use `Content-Type: application/json` for requests with a JSON body (POST, PUT).
*   **Responses:** Expect JSON responses (`application/json`).
    *   Success: `{"success": true, "data": {...}}` with HTTP status 2xx.
    *   Error: `{"success": false, "error": {"code": "...", "message": "...", "details": "..."}}` with HTTP status 4xx or 5xx.
*   **Documentation:** For detailed endpoint specifications, parameters, and examples (cURL, Python, Postman), access the built-in documentation served by the API itself (if running):
    *   `http://<your-base-url>/api/v1/docs` (e.g., `http://localhost:80/api/v1/docs` or `http://localhost:8080/api/v1/docs`)
    *   Alternatively, open the `docs.html` file directly in your browser if it's included in the repository.

## API vs. Standard Git/GitHub Comparison

This API is designed for specific automation tasks, not as a full replacement for Git or the GitHub UI/GraphQL API.

| Feature/Action              | gh-manager API Server                                     | Standard Git CLI / GitHub UI / SDKs                             | Key Difference / Use Case                                     |
| :-------------------------- | :-------------------------------------------------------- | :-------------------------------------------------------------- | :------------------------------------------------------------ |
| **Creating Repo**           | `POST /repo/create` (Simple JSON body)                    | `gh repo create`, UI Click, SDK functions                       | API simplifies basic programmatic creation.                     |
| **File Upload/Update/Delete** | `POST/PUT/DELETE /repo/.../file[/path]` (JSON body)     | `git add/commit/push`, UI Editor, SDK file methods              | API avoids local clone/pull/push; atomic operations per file. |
| **Creating Releases**       | `POST /repo/.../release` (Needs *existing* Git tag)       | `git tag && git push --tags`, `gh release create`, UI, SDKs     | API creates *metadata* for an existing tag; Git creates tag.  |
| **Uploading Release Assets**| **Two Steps:** 1. `POST /.../asset` (Prepare) 2. Run `upload.py` | `gh release upload`, UI Drag & Drop, SDK upload methods         | **API uses helper script for security (server doesn't touch files).** Others handle upload directly. |
| **Authentication**          | PAT via `X-API-Token` or `Authorization: Bearer` header | SSH Key, PAT, GitHub App Auth, Device Flow, etc.                | API uses simple token header auth suitable for scripts.         |
| **Complexity**              | Lower for *specific supported tasks*. Simple HTTP calls.  | Higher overall; requires Git knowledge or more complex SDK setup. | API abstracts complexity for its targeted features.             |
| **Dependency**              | HTTP Client (curl, requests, etc.)                        | Git CLI installed, GitHub CLI (`gh`), full SDK library        | API requires only basic HTTP capability on the client.          |
| **Use Case Focus**          | Automation, CI/CD tasks, simple backend integrations.     | Full development workflow, complex interactions, UI usage.      | API is task-oriented automation; Git/UI is user workflow.     |
| **Offline Operations**      | No (Requires running server & GitHub connection)          | Yes (Git allows local commits, branching, etc.)                 | Git excels offline; API requires connectivity.                |
| **Error Handling**          | Structured JSON errors, HTTP status codes.                | CLI exit codes/stderr, UI messages, SDK exceptions              | API provides machine-readable error details.                  |

## Configuration

The server primarily uses the following environment variable (or value from a `.env` file):

*   **`FLASK_DEBUG`**: Enables/disables Flask's debug mode.
    *   Default: `false` (as loaded by `load_dotenv()`, which defaults to false if not set)
    *   Set to `true` or `1` for development (enables auto-reloading and detailed error pages).
    *   **Warning:** Never run with `FLASK_DEBUG=true` in a production environment due to security risks.

*Note on Host/Port:*
When running directly with `python server.py`, the host and port are currently hardcoded in the `if __name__ == '__main__':` block to `0.0.0.0` and `80` respectively.
Environment variables `HOST` and `PORT` or a `.env` file will *not* affect this direct run method *unless* you modify the `server.py` script to read them again within that block.
You *can* still override the host and port when using the `flask run` command (e.g., `flask run --host=127.0.0.1 --port=5000`).
When using Docker, the host/port *inside* the container are usually set via the Dockerfile/docker-compose and mapped to the host machine via the `ports` directive in `docker-compose.yml`.

*Note on GitHub PAT:*
The GitHub PAT is **not** configured via environment variables for the *server itself*. It must be provided by the client in the headers of each API request.

## Error Handling

The API uses standard HTTP status codes and provides detailed error information in the JSON response body for non-2xx statuses:

```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE_STRING", // e.g., "AUTH_FAILED", "RESOURCE_NOT_FOUND"
    "message": "Human-readable explanation of the error.",
    "details": { ... } // Optional: More specific info, often from GitHub API
  }
}


Refer to the Core Concepts section in the full API documentation (docs.html or /api/v1/docs) for common status codes and custom error codes.

Contributing

Contributions are welcome! Please feel free to open an issue or submit a pull request.
(Optional: Add more detailed contribution guidelines if needed).

License

This project is licensed under the MIT License - see the LICENSE file 
