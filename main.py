import html
import os
from datetime import datetime

import requests
import string
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from enum import Enum
from html.parser import HTMLParser


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

class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def get_text(self):
        return " ".join(self.parts)


def html_to_text(html_content: str) -> str:
    """
    Convert Trilium HTML content into searchable plain text.
    """

    parser = HTMLTextExtractor()
    parser.feed(html_content)

    return parser.get_text().strip()

def normalize_name(text: str) -> str:
    remove = string.punctuation + string.whitespace

    return "".join(
        c for c in text.casefold()
        if c not in remove
    )

def string_compare(s1:str, s2: str):
    return normalize_name(s1) == normalize_name(s2)


def get_category_obj(category: str):
        
    category_obj = find_child_by_name(
        ROOT_FOLDER_ID,
        category
    )

    if not category_obj:
        raise HTTPException(
        status_code=404,
        detail=f"Category '{category}' not found",  
    )
        

    return category_obj

def get_folder_objekt(category_id:str, folder:str):
    folder_obj = find_child_by_name(
        category_id,
        folder
    )
    
    if not folder_obj:
        raise HTTPException(
        status_code=404,
        detail=f"folder '{folder}' not found"
    )

    return folder_obj



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


def get_children(parent_note_id: str):
    """
    Return all direct children beneath PROJECT_PARENT_ID.
    Each direct child is treated as a project.
    """

    parent = get_trilium_note(parent_note_id)
    children = []

    for child_id in parent.get("childNoteIds", []):
        child = get_trilium_note(child_id)

        children.append({
            "title": child["title"],
            "noteId": child["noteId"]
        })

    return children


def find_project_by_name(project_name: str):
    """
    Find a project using a case-insensitive exact title match.
    """

    normalized_name = project_name.strip().casefold()


    matches = [
        project
        for project in get_children(ROOT_FOLDER_ID)
        if string_compare(project["title"].strip().casefold(), normalized_name) 
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
        "title": created["title"],
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
            "title": existing["title"],
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
        "title": result["note"]["title"],
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
                "project": project["title"],
                "triliumStatus": response.status_code,
                "triliumResponse": response.text
            }
        )

    result = response.json()

    return {
        "action": "created",
        "project": project["title"],
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

def build_tree():
    """
    Build the hierarchy:

    Root
      -> Category
        -> Folder
          -> Notes

    Note IDs are preserved so other functions do not need
    to search Trilium again to resolve each note.
    """

    tree = {}

    categories = get_children(
        ROOT_FOLDER_ID
    )

    for category in categories:
        category_title = category["title"]

        tree[category_title] = {
            "noteId": category["noteId"],
            "folders": {}
        }

        folders = get_children(
            category["noteId"]
        )

        for folder in folders:
            folder_title = folder["title"]

            notes = get_children(
                folder["noteId"]
            )

            tree[category_title]["folders"][folder_title] = {
                "noteId": folder["noteId"],
                "notes": [
                    {
                        "title": note["title"],
                        "noteId": note["noteId"]
                    }
                    for note in notes
                ]
            }

    return tree

def create_search_snippet(
    text: str,
    search_term: str,
    surrounding_characters: int = 100
) -> str:
    """
    Return text surrounding the search match.
    """

    normalized_text = text.casefold()
    normalized_term = search_term.strip().casefold()

    match_position = normalized_text.find(
        normalized_term
    )

    if match_position == -1:
        return text[: surrounding_characters * 2].strip()

    start = max(
        0,
        match_position - surrounding_characters
    )

    end = min(
        len(text),
        match_position
        + len(search_term)
        + surrounding_characters
    )

    snippet = text[start:end].strip()

    if start > 0:
        snippet = f"...{snippet}"

    if end < len(text):
        snippet = f"{snippet}..."

    return snippet

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
    return get_children(ROOT_FOLDER_ID)

@app.get("/get-folders")
def list_folders(category: str):

    category_obj = get_category_obj(category)

    return get_children(category_obj["noteId"])


@app.get("/get-notes")
def list_notes(
    category:str,
    folder: str
):  
    category_obj = get_category_obj(category)

    folder_obj = get_folder_objekt(category_obj["noteId"], folder)

    return get_children(folder_obj["noteId"])

@app.get("/tree")
def list_tree_structure():
    return build_tree()

@app.get("/read-note")
def read_note(
    category: str,
    folder: str,
    title: str
): 
        #
    # Root
    #   └── Projekts
    #
    category_obj = get_category_obj(category)

    #
    # Projekts
    #   └── R7
    #

    folder_obj = get_folder_objekt(category_obj["noteId"], folder)
    #
    # R7
    #   └── Uppstartsmöte
    #

    note_obj = find_child_by_name(
        folder_obj["noteId"],
        title
    )

    if not note_obj:
        raise HTTPException(
        status_code=404,
        detail=f"Note '{title}' not found"
    )

    content = get_note_content(note_obj["noteId"])

    return {
        "action": "read",
        "category": category_obj["title"],
        "folder": folder_obj["title"],
        "title": note_obj["title"],
        "content": content
    }

@app.get("/search")
def search_notes(
    search_term: str,
    include_content: bool = True
):
    """
    Search category names, folder names, note titles,
    and optionally note content.

    Examples:

    /search?search_term=R7

    /search?search_term=Jenny

    /search?search_term=Sprint&include_content=false
    """

    clean_search_term = search_term.strip()

    if not clean_search_term:
        raise HTTPException(
            status_code=400,
            detail="search_term cannot be empty"
        )

    normalized_search = normalize_name(
        clean_search_term
    )

    tree = build_tree()
    matches = []

    for category_title, category_data in tree.items():

        folders = category_data["folders"]

        for folder_title, folder_data in folders.items():

            notes = folder_data["notes"]

            for note in notes:
                note_title = note["title"]
                note_id = note["noteId"]

                title_path_text = " ".join([
                    category_title,
                    folder_title,
                    note_title
                ])

                path_matches = (
                    normalized_search
                    in normalize_name(title_path_text)
                )

                content = ""
                plain_content = ""
                content_matches = False

                if include_content:
                    content = get_note_content(note_id)
                    plain_content = html_to_text(content)

                    content_matches = (
                        normalized_search
                        in normalize_name(plain_content)
                    )

                if path_matches or content_matches:

                    match_locations = []

                    if (
                        normalized_search
                        in normalize_name(category_title)
                    ):
                        match_locations.append("category")

                    if (
                        normalized_search
                        in normalize_name(folder_title)
                    ):
                        match_locations.append("folder")

                    if (
                        normalized_search
                        in normalize_name(note_title)
                    ):
                        match_locations.append("title")

                    if content_matches:
                        match_locations.append("content")

                    result = {
                        "category": category_title,
                        "categoryNoteId": category_data["noteId"],
                        "folder": folder_title,
                        "folderNoteId": folder_data["noteId"],
                        "title": note_title,
                        "noteId": note_id,
                        "matchedIn": match_locations
                    }

                    if content_matches:
                        result["snippet"] = create_search_snippet(
                            plain_content,
                            clean_search_term
                        )

                    matches.append(result)

    return {
        "searchTerm": clean_search_term,
        "includeContent": include_content,
        "count": len(matches),
        "matches": matches
    }


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
        "category": category["title"],
        "folder": folder["title"],
        "title": created["title"],
        "noteId": created["noteId"]
    }



