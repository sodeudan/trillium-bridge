import html
import os
from datetime import datetime

import requests
import string
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from enum import Enum


# Load variables from .env
load_dotenv()


app = FastAPI(
    title="Trilium Copilot Bridge",
    description="Creates and updates Trilium notes by project name.",
    version="0.2.0"
)


TRILIUM_URL = "http://127.0.0.1:37840"
TOKEN = os.getenv("TRILIUM_TOKEN")
ROOT_FOLDER_ID = "root"

REQUEST_TIMEOUT = 10

class WorkspaceType(str, Enum):
    project = "project"
    work_area = "workArea"

class Note(BaseModel):
    category: str = Field(min_length=1)
    folder: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)

def normalize_name(text: str) -> str:
    remove = string.punctuation + string.whitespace

    return "".join(
        c for c in text.casefold()
        if c not in remove
    )

def string_compare(s1:str, s2: str):
    return normalize_name(s1) == normalize_name(s2)


def trilium_headers():
    """
    Return the authorization header required by Trilium ETAPI.
    """

    if not TOKEN:
        raise HTTPException(
            status_code=500,
            detail="TRILIUM_TOKEN is not configured"
        )

    return {
        "Authorization": TOKEN
    }


def request_trilium(
    method: str,
    path: str,
    headers: dict | None = None,
    **kwargs
):
    """
    Send a request to Trilium and convert connection failures
    into useful FastAPI errors.
    """

    url = f"{TRILIUM_URL}{path}"

    combined_headers = {
        **trilium_headers(),
        **(headers or {})
    }

    try:
        response = requests.request(
            method=method,
            url=url,
            headers=combined_headers,
            timeout=REQUEST_TIMEOUT,
            **kwargs
        )

    except requests.ConnectionError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Could not connect to Trilium",
                "url": url,
                "error": str(error)
            }
        ) from error

    except requests.Timeout as error:
        raise HTTPException(
            status_code=504,
            detail={
                "message": "The Trilium request timed out",
                "url": url
            }
        ) from error

    return response


def get_trilium_note(note_id: str):
    """
    Retrieve a Trilium note's metadata.
    """

    response = request_trilium(
        "GET",
        f"/etapi/notes/{note_id}"
    )

    if not response.ok:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Could not retrieve the Trilium note",
                "noteId": note_id,
                "triliumStatus": response.status_code,
                "triliumResponse": response.text
            }
        )

    return response.json()


def get_note_content(note_id: str):
    """
    Retrieve the HTML content of a Trilium note.
    """

    response = request_trilium(
        "GET",
        f"/etapi/notes/{note_id}/content"
    )

    if not response.ok:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Could not retrieve the Trilium note content",
                "noteId": note_id,
                "triliumStatus": response.status_code,
                "triliumResponse": response.text
            }
        )

    return response.text


def get_projects():
    """
    Return all direct children beneath PROJECT_PARENT_ID.
    Each direct child is treated as a project.
    """

    project_parent = get_trilium_note(ROOT_FOLDER_ID)
    projects = []

    for child_id in project_parent.get("childNoteIds", []):
        child = get_trilium_note(child_id)

        projects.append({
            "name": child["title"],
            "noteId": child["noteId"]
        })

    return projects


def find_project_by_name(project_name: str):
    """
    Find a project using a case-insensitive exact title match.
    """

    normalized_name = project_name.strip().casefold()


    matches = [
        project
        for project in get_projects()
        if string_compare(project["name"].strip().casefold(), normalized_name) 
    ]

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"More than one project is named "
                    f"'{project_name.strip()}'"
                ),
                "matches": matches
            }
        )

    return None

def find_child_by_name(
    parent_note_id: str,
    child_name: str
):
    parent = get_trilium_note(parent_note_id)

    normalized_name = child_name.strip().casefold()

    matches = []

    for child_id in parent.get("childNoteIds", []):

        child = get_trilium_note(child_id)

        if string_compare(child["title"].strip().casefold(), normalized_name):
            matches.append(child)

    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "message":
                    f"More than one child named "
                    f"'{child_name}' exists"
            }
        )

    if len(matches) == 1:
        return matches[0]

    return None

def create_child_folder(
    parent_note_id: str,
    folder_name: str
):
    payload = {
        "parentNoteId": parent_note_id,
        "title": folder_name,
        "type": "text",
        "content": "",
        "isExpanded": True
    }

    response = request_trilium(
        "POST",
        "/etapi/create-note",
        json=payload
    )

    if not response.ok:
        raise HTTPException(
            status_code=502,
            detail=response.text
        )

    created = response.json()["note"]

    return {
        "name": created["title"],
        "noteId": created["noteId"],
        "created": True
    }

def find_or_create_child_folder(
    parent_note_id: str,
    folder_name: str
):
    existing = find_child_by_name(
        parent_note_id,
        folder_name
    )

    if existing:
        return {
            "name": existing["title"],
            "noteId": existing["noteId"],
            "created": False
        }

    return create_child_folder(
        parent_note_id,
        folder_name
    )

def create_project(project_name: str):
    """
    Create a new project beneath PROJECT_PARENT_ID.
    """

    clean_project_name = project_name.strip()

    if not clean_project_name:
        raise HTTPException(
            status_code=400,
            detail="The project name cannot be empty"
        )

    # Check again to reduce accidental duplicate creation.
    existing_project = find_project_by_name(clean_project_name)

    if existing_project:
        return existing_project

    payload = {
        "parentNoteId": ROOT_FOLDER_ID,
        "title": clean_project_name,
        "type": "text",
        "content": (
            f"<h1>{html.escape(clean_project_name)}</h1>"
            "<p>Project created automatically.</p>"
        )
    }

    response = request_trilium(
        "POST",
        "/etapi/create-note",
        json=payload
    )

    if not response.ok:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Trilium could not create the project",
                "project": clean_project_name,
                "triliumStatus": response.status_code,
                "triliumResponse": response.text
            }
        )

    result = response.json()

    return {
        "name": result["note"]["title"],
        "noteId": result["note"]["noteId"],
        "created": True
    }


def find_or_create_project(project_name: str):
    """
    Find an existing project or create it automatically.
    """

    project = find_project_by_name(project_name)

    if project:
        return {
            **project,
            "created": False
        }

    return create_project(project_name)


def find_note_by_title(project_name: str, note_title: str):
    """
    Find a direct child note inside a project using a
    case-insensitive exact title match.
    """

    project = find_project_by_name(project_name)

    if not project:
        return None

    project_note = get_trilium_note(project["noteId"])
    normalized_title = note_title.strip().casefold()
    matches = []

    for child_id in project_note.get("childNoteIds", []):
        child = get_trilium_note(child_id)

        if child["title"].strip().casefold() == normalized_title:
            matches.append(child)

    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"More than one note named '{note_title.strip()}' "
                    f"exists in project '{project_name.strip()}'"
                ),
                "matches": [
                    {
                        "title": match["title"],
                        "noteId": match["noteId"]
                    }
                    for match in matches
                ]
            }
        )

    if len(matches) == 1:
        return matches[0]

    return None


def create_trilium_note(note: Note, project: dict):
    """
    Create a new note beneath the supplied project.
    """

    payload = {
        "parentNoteId": project["noteId"],
        "title": note.title.strip(),
        "type": "text",
        "content": f"<p>{html.escape(note.content)}</p>"
    }

    response = request_trilium(
        "POST",
        "/etapi/create-note",
        json=payload
    )

    if not response.ok:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Trilium could not create the note",
                "project": project["name"],
                "triliumStatus": response.status_code,
                "triliumResponse": response.text
            }
        )

    result = response.json()

    return {
        "action": "created",
        "project": project["name"],
        "projectCreated": project.get("created", False),
        "projectNoteId": project["noteId"],
        "title": result["note"]["title"],
        "noteId": result["note"]["noteId"]
    }


def append_to_trilium_note(note: Note, existing_note: dict):
    """
    Append new HTML content to an existing Trilium note.
    """

    note_id = existing_note["noteId"]
    current_content = get_note_content(note_id)

    timestamp = datetime.now().astimezone().strftime(
        "%Y-%m-%d %H:%M"
    )

    new_section = (
        "<hr>"
        f"<p><strong>Update {html.escape(timestamp)}</strong></p>"
        f"<p>{html.escape(note.content)}</p>"
    )

    updated_content = current_content + new_section
    content_bytes = updated_content.encode("utf-8")

    response = request_trilium(
        "PUT",
        f"/etapi/notes/{note_id}/content",
        headers={
            "Content-Type": "application/octet-stream"
        },
        data=content_bytes
    )

    if not response.ok:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Trilium could not update the note",
                "noteId": note_id,
                "triliumStatus": response.status_code,
                "triliumResponse": response.text
            }
        )

    return {
        "action": "updated",
        "category": note.category.strip(),
        "folder": note.folder.strip(),
        "title": existing_note["title"],
        "noteId": note_id
    }


@app.get("/")
def root():
    return {
        "status": "working",
        # "projectParentId": PROJECT_PARENT_ID
    }


@app.get("/trilium-test")
def trilium_test():
    response = request_trilium(
        "GET",
        "/etapi/app-info"
    )

    if not response.ok:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Trilium connection test failed",
                "triliumStatus": response.status_code,
                "triliumResponse": response.text
            }
        )

    return {
        "status": "connected",
        "trilium": response.json()
    }


@app.get("/projects")
def list_projects():
    return get_projects()


@app.post("/write-note")
def write_note(note: Note):

    #
    # Root
    #   └── Projekts
    #

    category = find_or_create_child_folder(
        ROOT_FOLDER_ID,
        note.category
    )

    #
    # Projekts
    #   └── R7
    #

    folder = find_or_create_child_folder(
        category["noteId"],
        note.folder
    )

    #
    # R7
    #   └── Uppstartsmöte
    #

    existing_note = find_child_by_name(
        folder["noteId"],
        note.title
    )

    if existing_note:
        return append_to_trilium_note(
            note,
            existing_note
        )

    payload = {
        "parentNoteId": folder["noteId"],
        "title": note.title,
        "type": "text",
        "content": f"<p>{html.escape(note.content)}</p>"
    }

    response = request_trilium(
        "POST",
        "/etapi/create-note",
        json=payload
    )

    created = response.json()["note"]

    return {
        "action": "created",
        "category": category["name"],
        "folder": folder["name"],
        "title": created["title"],
        "noteId": created["noteId"]
    }