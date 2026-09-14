# Circle

Circle is a Reddit-style community platform. This repository currently contains
the first small piece of the backend: a FastAPI service with a health-check
endpoint.

## Project structure

```text
app/
	__init__.py
	main.py          # Creates the FastAPI app and API endpoints
	database.py      # SQLite connection and database sessions
	models.py        # Database table definitions, including users
requirements.txt  # Python dependencies
```

## Run the backend

Create and activate a virtual environment, then install the dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start the development server:

```bash
uvicorn app.main:app --reload
```

The API will be available at `http://127.0.0.1:8000`. Visit
`http://127.0.0.1:8000/health` to check that it is running. The response is:

```json
{"status": "ok"}
```

FastAPI also provides interactive API documentation at
`http://127.0.0.1:8000/docs`.

## Database setup

Circle currently uses a local SQLite database named `circle.db`. The file is
created automatically when the FastAPI server starts. The initial database
table is `users`, with columns for an ID, username, email, and creation time.

The API supports user creation and listing through `/api/users`. Registration
and login are available through `/api/auth/register` and `/api/auth/login`.
Passwords are stored as secure hashes, and login returns a JWT access token.