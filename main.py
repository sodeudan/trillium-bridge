import html
import os
from datetime import datetime

import requests
import string
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from enum import Enum
from html.parser import HTMLParser


# Load variables from .env
load_dotenv()


app = FastAPI(
    title="Trilium Copilot Bridge",
    description="Creates and updates Trilium notes by project name.",
    version="0.2.0",
    # servers=[{
    #     "url": "https://specific-liverpool-isle-identifier.trycloudflare.com/"
    # }]
)


TRILIUM_URL = "http://127.0.0.1:37840"
TOKEN = os.getenv("TRILIUM_TOKEN")
ROOT_FOLDER_ID = "root"

REQUEST_TIMEOUT = 10


class NotePath(BaseModel):
    path: list[str] = Field(
        min_length=1,
        description=(
            "Complete path from root to the target note. "
            "The last element is the note title. "
            "Example: ['Projekts', 'R7', 'Förarbete', "
            "'Uppdragsbeskrivning']"
        )
    )


class WriteNoteRequest(NotePath):
    content: str = Field(
        min_length=1,
        description="Content to create or append to the note"
    )



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


def get_children(parent_note_id: str) -> list:
    """
    Return the direct children of any Trilium note.
    """

    parent = get_trilium_note(parent_note_id)
    children = []

    for child_id in parent.get("childNoteIds", []):
        child = get_trilium_note(child_id)

        children.append({
            "title": child["title"],
            "noteId": child["noteId"],
            "type": child.get("type"),
            "mime": child.get("mime")
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
) -> dict | None:
    """
    Find a direct child beneath a parent.

    Uses normalize_name(), so R7, R 7 and r-7 match.
    """

    normalized_target = normalize_name(child_name)

    matches = [
        child
        for child in get_children(parent_note_id)
        if normalize_name(child["title"]) == normalized_target
    ]

    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"More than one child matches "
                    f"'{child_name}' under '{parent_note_id}'"
                ),
                "matches": matches
            }
        )

    if len(matches) == 1:
        return matches[0]

    return None


def create_child_container(
    parent_note_id: str,
    title: str
) -> dict:
    """
    Create a new container beneath any parent.
    """

    clean_title = title.strip()

    if not clean_title:
        raise HTTPException(
            status_code=400,
            detail="A path component cannot be empty"
        )

    payload = {
        "parentNoteId": parent_note_id,
        "title": clean_title,
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
            detail={
                "message": "Trilium could not create the container",
                "parentNoteId": parent_note_id,
                "title": clean_title,
                "triliumStatus": response.status_code,
                "triliumResponse": response.text
            }
        )

    created = response.json()["note"]

    return {
        "title": created["title"],
        "noteId": created["noteId"],
        "created": True
    }

def find_or_create_path(
    path: list[str]
) -> dict:
    """
    Walk from root through every path component.

    Existing containers are reused. Missing containers are created.
    Returns the final path node.
    """

    if not path:
        raise HTTPException(
            status_code=400,
            detail="The path must contain at least one item"
        )

    current_parent_id = ROOT_FOLDER_ID
    resolved_path = []
    anything_created = False

    for path_component in path:
        clean_component = path_component.strip()

        if not clean_component:
            raise HTTPException(
                status_code=400,
                detail="Path components cannot be empty"
            )

        child = find_child_by_name(
            current_parent_id,
            clean_component
        )

        if child:
            resolved = {
                **child,
                "created": False
            }
        else:
            resolved = create_child_container(
                current_parent_id,
                clean_component
            )

            anything_created = True

        resolved_path.append({
            "title": resolved["title"],
            "noteId": resolved["noteId"],
            "created": resolved["created"]
        })

        current_parent_id = resolved["noteId"]

    return {
        "title":(
            resolved_path[-1]["title"]
            if resolved_path
            else "root"
            ),
        "noteId": resolved_path[-1]["noteId"],
        "created": anything_created,
        "resolvedPath": resolved_path
    }

def find_path(path: list[str]) -> dict | None:
    """
    Traverse an existing path starting at root.

    Nothing is created when a path element is missing.
    """

    if not path:
        return {
            "title": "root",
            "noteId": ROOT_FOLDER_ID,
            "resolvedPath": []
        }

    current_parent_id = ROOT_FOLDER_ID
    resolved_path = []

    for path_part in path:
        clean_part = path_part.strip()

        if not clean_part:
            raise HTTPException(
                status_code=400,
                detail="Path elements cannot be empty"
            )

        child = find_child_by_name(
            current_parent_id,
            clean_part
        )

        if not child:
            return None

        resolved_path.append({
            "title": child["title"],
            "noteId": child["noteId"]
        })

        current_parent_id = child["noteId"]

    return {
        "title": resolved_path[-1]["title"],
        "noteId": resolved_path[-1]["noteId"],
        "resolvedPath": resolved_path
    }


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

def create_trilium_note(
    request: WriteNoteRequest,
    parent: dict,
    note_title: str
) -> dict:
    payload = {
        "parentNoteId": parent["noteId"],
        "title": note_title,
        "type": "text",
        "content": (
            f"<p>{html.escape(request.content)}</p>"
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
                "message": "Trilium could not create the note",
                "path": request.path,
                "triliumStatus": response.status_code,
                "triliumResponse": response.text
            }
        )

    created = response.json()["note"]

    return {
        "action": "created",
        "path": [
            item["title"]
            for item in parent["resolvedPath"]
        ] + [created["title"]],
        "parentPathCreated": parent["created"],
        "title": created["title"],
        "noteId": created["noteId"]
    }


def append_to_trilium_note(
    request: WriteNoteRequest,
    existing_note: dict,
    resolved_path: list[str]
) -> dict:
    note_id = existing_note["noteId"]
    current_content = get_note_content(note_id)

    timestamp = datetime.now().astimezone().strftime(
        "%Y-%m-%d %H:%M"
    )

    new_section = (
        "<hr>"
        f"<p><strong>Update "
        f"{html.escape(timestamp)}</strong></p>"
        f"<p>{html.escape(request.content)}</p>"
    )

    updated_content = current_content + new_section

    response = request_trilium(
        "PUT",
        f"/etapi/notes/{note_id}/content",
        headers={
            "Content-Type": "application/octet-stream"
        },
        data=updated_content.encode("utf-8")
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
        "path": resolved_path,
        "title": existing_note["title"],
        "noteId": note_id,
        "appendedCharacters": len(request.content)
    }

def build_tree_node(
    parent_note_id: str,
    visited: set[str] | None = None
) -> list:
    """
    Recursively build every descendant below a parent.

    A path-local visited set prevents cycles if a Trilium note
    is branched into one of its own descendant paths.
    """

    if visited is None:
        visited = set()

    if parent_note_id in visited:
        return [{
            "title": "[Cycle detected]",
            "noteId": parent_note_id,
            "children": []
        }]

    current_visited = visited | {parent_note_id}
    result = []

    for child in get_children(parent_note_id):
        result.append({
            "title": child["title"],
            "noteId": child["noteId"],
            "children": build_tree_node(
                child["noteId"],
                current_visited
            )
        })

    return result

def search_tree_recursive(
    parent_note_id: str,
    search_term: str,
    current_path: list[str] | None = None,
    include_content: bool = True,
    visited: set[str] | None = None
) -> list[dict]:
    if current_path is None:
        current_path = []

    if visited is None:
        visited = set()

    if parent_note_id in visited:
        return []

    current_visited = visited | {parent_note_id}
    normalized_search = normalize_name(search_term)
    matches = []

    for child in get_children(parent_note_id):
        child_path = current_path + [child["title"]]

        title_matches = (
            normalized_search
            in normalize_name(child["title"])
        )

        plain_content = ""
        content_matches = False

        if include_content:
            raw_content = get_note_content(
                child["noteId"]
            )

            plain_content = html_to_text(
                raw_content
            )

            content_matches = (
                normalized_search
                in normalize_name(plain_content)
            )

        if title_matches or content_matches:
            match = {
                "path": child_path,
                "title": child["title"],
                "noteId": child["noteId"],
                "matchedIn": []
            }

            if title_matches:
                match["matchedIn"].append("title")

            if content_matches:
                match["matchedIn"].append("content")
                match["snippet"] = create_search_snippet(
                    plain_content,
                    search_term
                )

            matches.append(match)

        matches.extend(
            search_tree_recursive(
                parent_note_id=child["noteId"],
                search_term=search_term,
                current_path=child_path,
                include_content=include_content,
                visited=current_visited
            )
        )

    return matches

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

def count_tree(nodes):

    folders = 0
    notes = 0

    for node in nodes:

        folders += 1

        child_folders, child_notes = count_tree(
            node["children"]
        )

        folders += child_folders
        notes += child_notes

        #
        # A note has no children
        #

        if len(node["children"]) == 0:
            notes += 1

    return folders, notes


@app.get("/")
def root():
    return {
        "status": "working",
        # "projectParentId": PROJECT_PARENT_ID
    }


@app.get("/trilium-test",
         summary = "tests the Trilium connection, returns app info")
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


@app.get("/projects",
         summary = "Lists all categories and with title and noteId")
def list_projects():
    return get_children(ROOT_FOLDER_ID)

@app.get("/get-folders",
         summary = "Lists all folders and with title and noteId")
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
    return {
        "title": "root",
        "noteId": ROOT_FOLDER_ID,
        "children": build_tree_node(
            ROOT_FOLDER_ID
        )
    }


@app.post(
    "/read-note",
    summary="Read a note using its complete path"
)
def read_note(request: NotePath):
    clean_path = [
        part.strip()
        for part in request.path
        if part.strip()
    ]

    if not clean_path:
        raise HTTPException(
            status_code=400,
            detail="The path must contain a note title"
        )

    note = find_path(
        clean_path
    )

    if not note:
        raise HTTPException(
            status_code=404,
            detail={
                "message": "Note path was not found",
                "path": clean_path
            }
        )

    return {
        "action": "read",
        "path": [
            item["title"]
            for item in note["resolvedPath"]
        ],
        "title": note["title"],
        "noteId": note["noteId"],
        "content": get_note_content(
            note["noteId"]
        )
    }

@app.get("/search")
def search_notes(
    search_term: str,
    include_content: bool = True
):
    clean_term = search_term.strip()

    if not clean_term:
        raise HTTPException(
            status_code=400,
            detail="search_term cannot be empty"
        )

    matches = search_tree_recursive(
        parent_note_id=ROOT_FOLDER_ID,
        search_term=clean_term,
        include_content=include_content
    )

    return {
        "searchTerm": clean_term,
        "includeContent": include_content,
        "count": len(matches),
        "matches": matches
    }

@app.get(
    "/statistics",
    summary="Get the current statistics"
)
def statistics():

    tree = build_tree_node(
        ROOT_FOLDER_ID
    )

    folders, notes = count_tree(
        tree
    )

    return {
        "nodes": folders,
        "leafNotes": notes
    }
        

@app.post(
    "/write-note",
    summary="Create or update a note using a complete path"
)
def write_note(request: WriteNoteRequest):
    clean_path = [
        part.strip()
        for part in request.path
        if part.strip()
    ]

    if not clean_path:
        raise HTTPException(
            status_code=400,
            detail="The path must contain a note title"
        )

    # Final element is the note title.
    note_title = clean_path[-1]

    # Everything before it is the parent hierarchy.
    parent_path = clean_path[:-1]

    parent = find_or_create_path(
        parent_path
    )

    existing_note = find_child_by_name(
        parent["noteId"],
        note_title
    )

    resolved_full_path = [
        item["title"]
        for item in parent["resolvedPath"]
    ] + [
        existing_note["title"]
        if existing_note
        else note_title
    ]

    if existing_note:
        return append_to_trilium_note(
            request=request,
            existing_note=existing_note,
            resolved_path=resolved_full_path
        )

    return create_trilium_note(
        request=request,
        parent=parent,
        note_title=note_title
    )



